"""M7 editor API. Org comes from the verified membership (never the body); every lookup is org-scoped and backed by RLS."""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.editor import commands as C, preflight, regeneration, service
from app.models import CreativeDocument, CreativeVersion, GeneratedCreative, RegenerationJob, RenderJob, Role
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage, get_storage
from app.workers import tasks

router = APIRouter(tags=["editor"])
ERR_STATUS = {"layer_not_found": 404, "asset_not_found": 422, "layer_locked": 423, "nothing_to_undo": 409, "nothing_to_redo": 409,
              "job_not_ready": 409, "job_closed": 409, "target_missing": 409, "target_changed": 409, "candidate_not_found": 404}


def err(request: Request, status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}}, status_code=status)


def _creative(db, ctx, crid) -> GeneratedCreative | None:
    return OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)


def _doc(db, ctx, did) -> tuple[CreativeDocument, GeneratedCreative] | None:
    d = db.scalar(__import__("sqlalchemy").select(CreativeDocument).where(CreativeDocument.id == did, CreativeDocument.organization_id == ctx.org_id))
    c = _creative(db, ctx, d.creative_id) if d else None
    return (d, c) if d and c else None


def _not_found(request, what="Document"):
    return err(request, 404, "not_found", f"{what} not found")


def _ops_out(ops):
    return [{"id": str(o.id), "type": o.operation_type, "revision_before": o.revision_before, "revision_after": o.revision_after, "target_layer_id": o.target_layer_id} for o in ops]


# ---------------------------------------------------------------- documents
@router.post("/creatives/{crid}/document")
def open_document(crid: uuid.UUID, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Open (and lazily create) the editable document for one banner format. Each format has its own document."""
    c = _creative(db, ctx, crid)
    if not c:
        return _not_found(request, "Creative")
    doc = service.get_or_create(db, ctx.org_id, c, ctx.user.id)
    db.commit()
    return service.view(db, ctx.org_id, doc, storage)


@router.get("/creatives/{crid}/document")
def read_document(crid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Read-only lookup for roles that may view but not edit (never creates a document)."""
    from sqlalchemy import select
    d = db.scalar(select(CreativeDocument).where(CreativeDocument.creative_id == crid, CreativeDocument.organization_id == ctx.org_id))
    return service.view(db, ctx.org_id, d, storage) if d else _not_found(request)


@router.get("/creative-documents/{did}")
def get_document(did: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    r = _doc(db, ctx, did)
    return service.view(db, ctx.org_id, r[0], storage) if r else _not_found(request)


@router.get("/campaigns/{cid}/creative-documents")
def list_documents(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    rows = OrgRepo(db, CreativeDocument, ctx.org_id).list(campaign_id=cid)
    return [{"id": str(d.id), "creative_id": str(d.creative_id), "format_id": d.format_id, "name": d.name, "width": d.width, "height": d.height, "revision": d.revision} for d in rows]


class PatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(draft|ready)$")


@router.patch("/creative-documents/{did}")
def patch_document(did: uuid.UUID, body: PatchIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Metadata only. Design content changes go through /operations so every change is a validated, logged, undoable command."""
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    d = r[0]
    if body.name is not None: d.name = body.name
    if body.status is not None: d.status = body.status
    db.commit()
    return service.view(db, ctx.org_id, d, storage)


class OpsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    commands: list[dict] = Field(min_length=1, max_length=100)
    batch_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")   # idempotency key for safe retries


def _run_write(request, ctx, db, storage, did, fn):
    locked = service.lock(db, ctx.org_id, did)
    c = _creative(db, ctx, locked.creative_id) if locked else None
    if not locked or not c:
        return _not_found(request)
    try:
        ops, dup = fn(locked, c)
    except service.RevisionConflict as e:
        db.rollback()
        fresh = service.lock(db, ctx.org_id, did)
        return JSONResponse({"error": {"code": "revision_conflict", "message": "The document changed since you loaded it", "request_id": request.state.request_id,
                                       "current_revision": e.current}, "document": service.view(db, ctx.org_id, fresh, storage)}, status_code=409)
    except C.CommandError as e:
        db.rollback()
        return err(request, ERR_STATUS.get(e.code, 422), e.code, e.message)
    db.commit()
    return {"document": service.view(db, ctx.org_id, locked, storage), "operations": _ops_out(ops), "duplicate": dup}


@router.post("/creative-documents/{did}/operations")
def post_operations(did: uuid.UUID, body: OpsIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    return _run_write(request, ctx, db, storage, did, lambda d, c: service.apply_batch(db, ctx.org_id, d, c, ctx.user.id, body.commands, body.expected_revision, batch_id=body.batch_id))


class RevIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(default=None, ge=0)


def _undo_redo(which):
    def endpoint(did: uuid.UUID, request: Request, body: RevIn | None = None, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
        def fn(d, c):
            if body and body.expected_revision is not None and body.expected_revision != d.revision:
                raise service.RevisionConflict(d.revision)
            return service.undo_redo(db, ctx.org_id, d, c, ctx.user.id, which), False
        return _run_write(request, ctx, db, storage, did, fn)
    return endpoint


router.post("/creative-documents/{did}/undo")(_undo_redo("undo"))
router.post("/creative-documents/{did}/redo")(_undo_redo("redo"))


@router.get("/creative-documents/{did}/history")
def get_history(did: uuid.UUID, request: Request, limit: int = 100, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    r = _doc(db, ctx, did)
    return service.history(db, r[0], min(max(limit, 1), 500)) if r else _not_found(request)


class DuplicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=50, le=4096)
    height: int = Field(ge=50, le=4096)


@router.post("/creative-documents/{did}/duplicate", status_code=201)
def duplicate_document(did: uuid.UUID, body: DuplicateIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Copy this design to a new banner size (new creative + new independent document, basic proportional fit). The source is untouched; nothing stays in sync."""
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    try:
        nd = service.duplicate_to_size(db, ctx.org_id, r[0], r[1], ctx.user.id, body.width, body.height, storage)
    except C.CommandError as e:
        db.rollback()
        return err(request, 422, e.code, e.message)
    db.commit()
    return JSONResponse({**service.view(db, ctx.org_id, nd, storage), "source_document_id": str(did)}, status_code=201)


# ---------------------------------------------------------------- versions
class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Saved version", min_length=1, max_length=200)
    summary: str = Field(default="", max_length=400)


@router.post("/creative-documents/{did}/versions", status_code=201)
def create_version(did: uuid.UUID, body: VersionIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    v = service.make_version(db, r[0], body.name, body.summary, "manual", ctx.user.id)
    db.commit()
    return service.version_out(v)


@router.get("/creative-documents/{did}/versions")
def list_versions(did: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    rows = db.scalars(__import__("sqlalchemy").select(CreativeVersion).where(CreativeVersion.document_id == did, CreativeVersion.organization_id == ctx.org_id)
                      .order_by(CreativeVersion.created_at.desc(), CreativeVersion.revision.desc())).all()
    return [service.version_out(v) for v in rows]


@router.post("/creative-documents/{did}/restore/{vid}")
def restore_version(did: uuid.UUID, vid: uuid.UUID, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    v = db.scalar(__import__("sqlalchemy").select(CreativeVersion).where(CreativeVersion.id == vid, CreativeVersion.document_id == did, CreativeVersion.organization_id == ctx.org_id))
    if not v:
        return _not_found(request, "Version")
    return _run_write(request, ctx, db, storage, did, lambda d, c: (service.restore(db, ctx.org_id, d, c, ctx.user.id, v), False))


# ---------------------------------------------------------------- preflight & rendering
@router.get("/creative-documents/{did}/preflight")
def get_preflight(did: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    return {"revision": r[0].revision, "warnings": preflight.run(db, ctx.org_id, r[0].document, storage)}


class RenderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_format: str = Field(default="png", pattern="^(png|jpeg|webp)$")
    quality: int = Field(default=92, ge=40, le=100)


def render_out(j: RenderJob, db, ctx, storage) -> dict:
    from app.models import CreativeAsset
    a = OrgRepo(db, CreativeAsset, ctx.org_id).get(j.output_asset_id) if j.output_asset_id else None
    return {"id": str(j.id), "document_id": str(j.document_id), "revision": j.revision, "output_format": j.output_format, "status": j.status, "warnings": j.warnings,
            "error_message": j.error_message, "output_asset_id": str(j.output_asset_id) if j.output_asset_id else None,
            "url": storage.signed_url(a.storage_key) if a else None, "width": a.width if a else None, "height": a.height if a else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None}


@router.post("/creative-documents/{did}/render", status_code=202)
def post_render(did: uuid.UUID, body: RenderIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    j = RenderJob(organization_id=ctx.org_id, document_id=did, revision=r[0].revision, output_format=body.output_format, quality=body.quality)
    db.add(j)
    db.commit()
    tasks.run_render_job.delay(str(ctx.org_id), str(j.id))
    db.refresh(j)
    return JSONResponse(render_out(j, db, ctx, storage), status_code=202)


@router.get("/render-jobs/{jid}")
def get_render_job(jid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    j = OrgRepo(db, RenderJob, ctx.org_id).get(jid)
    return render_out(j, db, ctx, storage) if j else _not_found(request, "Render job")


# ---------------------------------------------------------------- selected-element AI regeneration
class RegenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer_id: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=3, max_length=500)
    expected_revision: int | None = Field(default=None, ge=0)
    candidates: int = Field(default=2, ge=1, le=3)
    allow_ai_product: bool = False      # explicit opt-in: otherwise a product layer only ever gets derivatives of the ORIGINAL photo


def regen_out(j: RegenerationJob, db, ctx, storage, doc: CreativeDocument | None = None) -> dict:
    from app.models import CreativeAsset
    cands = []
    for c in j.candidates or []:
        item = dict(c)
        if "asset_id" in c:
            u = uuid.UUID(c["asset_id"])
            from app.models import ProductAsset
            a = OrgRepo(db, CreativeAsset, ctx.org_id).get(u) or OrgRepo(db, ProductAsset, ctx.org_id).get(u)
            if a:
                item.update(url=storage.signed_url(a.storage_key, 3600), width=a.width, height=a.height)
        cands.append(item)
    doc = doc or db.get(CreativeDocument, j.document_id)
    return {"id": str(j.id), "document_id": str(j.document_id), "target_layer_id": j.target_layer_id, "target_type": j.target_type, "prompt": j.prompt,
            "source_revision": j.source_revision, "status": j.status, "candidates": cands, "accepted_asset_id": str(j.accepted_asset_id) if j.accepted_asset_id else None,
            "provider": j.provider, "model": j.model, "usage": j.usage_metadata, "error_message": j.error_message, "current_revision": doc.revision,
            **regeneration.staleness(doc, j), "completed_at": j.completed_at.isoformat() if j.completed_at else None}


@router.post("/creative-documents/{did}/regenerate", status_code=202)
def post_regenerate(did: uuid.UUID, body: RegenIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    r = _doc(db, ctx, did)
    if not r:
        return _not_found(request)
    try:
        j = regeneration.request(db, ctx.org_id, r[0], ctx.user.id, body.layer_id, body.prompt, body.expected_revision)
    except service.RevisionConflict as e:
        return err(request, 409, "revision_conflict", "The document changed since you selected the layer", current_revision=e.current)
    except C.CommandError as e:
        db.rollback()
        return err(request, ERR_STATUS.get(e.code, 422), e.code, e.message)
    j.usage_metadata = {"n": body.candidates, "allow_ai_product": body.allow_ai_product}
    db.commit()
    tasks.run_regeneration_job.delay(str(ctx.org_id), str(j.id))
    db.refresh(j)
    return JSONResponse(regen_out(j, db, ctx, storage), status_code=202)


def _job(db, ctx, jid) -> RegenerationJob | None:
    return OrgRepo(db, RegenerationJob, ctx.org_id).get(jid)


@router.get("/regeneration-jobs/{jid}")
def get_regen_job(jid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    j = _job(db, ctx, jid)
    return regen_out(j, db, ctx, storage) if j else _not_found(request, "Job")


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_index: int = Field(default=0, ge=0, le=10)
    acknowledge_changes: bool = False     # the user reviewed the candidate against the *current* design after it changed


@router.post("/regeneration-jobs/{jid}/accept")
def accept_regen(jid: uuid.UUID, body: AcceptIn, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    j0 = _job(db, ctx, jid)
    if not j0:
        return _not_found(request, "Job")
    res = _run_write(request, ctx, db, storage, j0.document_id, lambda d, c: (regeneration.accept(db, ctx.org_id, _job(db, ctx, jid), d, c, ctx.user.id, body.candidate_index, body.acknowledge_changes), False))
    if isinstance(res, dict):
        res["job"] = regen_out(_job(db, ctx, jid), db, ctx, storage)
    return res


@router.post("/regeneration-jobs/{jid}/reject")
def reject_regen(jid: uuid.UUID, request: Request, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    j = _job(db, ctx, jid)
    if not j:
        return _not_found(request, "Job")
    try:
        regeneration.reject(db, j)
    except C.CommandError as e:
        return err(request, 409, e.code, e.message)
    db.commit()
    return regen_out(j, db, ctx, storage)


# ---------------------------------------------------------------- assets & fonts for the editor panels
@router.get("/campaigns/{cid}/editor-assets")
def editor_assets(cid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Everything the editor may place, all from the caller's own org: product images, logos, generated backgrounds, brand colours/fonts."""
    from app.models import BrandProfile, Campaign, CreativeAsset, Product, ProductAsset
    camp = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not camp:
        return _not_found(request, "Campaign")
    prod = db.query(Product).filter(Product.campaign_id == cid, Product.organization_id == ctx.org_id).first()
    pa = OrgRepo(db, ProductAsset, ctx.org_id).list(product_id=prod.id) if prod else []
    item = lambda a, kind: {"id": str(a.id), "kind": kind, "url": storage.signed_url(a.storage_key, 3600), "width": a.width, "height": a.height, "mime": a.mime}  # noqa: E731
    cas = OrgRepo(db, CreativeAsset, ctx.org_id).list(campaign_id=cid)
    bid = (camp.brief or {}).get("brand_profile_id")
    bp = OrgRepo(db, BrandProfile, ctx.org_id).get(uuid.UUID(bid)) if bid else None
    colors = ((bp.overrides or {}).get("colors") or (bp.detected or {}).get("colors") or []) if bp else []
    return {"products": [item(a, "product") for a in pa if a.kind == "image"], "logos": [item(a, "logo") for a in pa if a.kind == "logo"],
            "backgrounds": [item(a, "background") for a in cas if a.kind == "background"], "cutouts": [{**item(a, "cutout"), "parent_asset_id": str(a.parent_asset_id) if a.parent_asset_id else None} for a in cas if a.kind == "cutout"],
            "brand": {"colors": colors, "fonts": sorted({f for f, _ in __import__("app.rendering.fonts", fromlist=["REGISTRY"]).REGISTRY})}}


@router.get("/editor/fonts/{file}", include_in_schema=False)
def editor_font(file: str):
    """Serve ONLY the bundled TTFs (allowlist), so editor text measurement uses the very files the renderer uses. No client-supplied font URLs exist."""
    from fastapi.responses import FileResponse
    from app.rendering import fonts
    if file not in set(fonts.REGISTRY.values()):
        return JSONResponse({"error": {"code": "not_found", "message": "Unknown font"}}, status_code=404)
    return FileResponse(fonts.FONT_DIR / file, media_type="font/ttf", headers={"cache-control": "public, max-age=86400"})
