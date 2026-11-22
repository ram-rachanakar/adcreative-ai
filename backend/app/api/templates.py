"""M7 templates API. Org comes from the verified membership (never the body); visibility/ownership is enforced in templates.service and by RLS.
Errors use the editor's structured shape: {"error": {code, message, request_id}} + proper status codes."""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.editor import commands as C, service as editor
from app.models import CreativeDocument, GeneratedCreative, Role, Template
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage, get_storage
from app.templates import application as A, service as T
from app.templates.service import TemplateError

router = APIRouter(tags=["templates"])
DESIGNER = Depends(require_role(Role.designer))


def _err(request: Request, status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}}, status_code=status)


def _guard(request: Request, db: Session, fn):
    try:
        r = fn()
        db.commit()
        return r
    except TemplateError as e:
        db.rollback()
        return _err(request, e.status, e.code, e.message, **e.extra)
    except editor.RevisionConflict as e:
        db.rollback()
        return _err(request, 409, "revision_conflict", "The banner changed since this preview was created; preview again", current_revision=e.current)
    except C.CommandError as e:
        db.rollback()
        return _err(request, 422, e.code, e.message)


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- library
@router.get("/templates")
def list_templates(request: Request, q: str | None = Query(default=None, max_length=100), tab: str = Query(default="all", pattern="^(all|layouts|styles|industry|mine)$"),
                   industry: str | None = None, style: str | None = None, layout: str | None = None, aspect: str | None = Query(default=None, pattern="^(wide|landscape|square|portrait|tall)$"),
                   width: int | None = Query(default=None, ge=16, le=8192), height: int | None = Query(default=None, ge=16, le=8192),
                   owner: str | None = Query(default=None, pattern="^(system|organization|personal)$"), source: str | None = Query(default=None, pattern="^(built_in|manual|saved_from_design|duplicated)$"),
                   compatible_with: str | None = Query(default=None, pattern=r"^\d{2,4}x\d{2,4}$"), document_id: uuid.UUID | None = None, favorites: bool = False,
                   status: str | None = Query(default=None, pattern="^(draft|published|archived|all)$"), thumbnails: bool = True,
                   limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0),
                   ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        compat = tuple(int(x) for x in compatible_with.split("x")) if compatible_with else None     # type: ignore[assignment]
        only_compatible = bool(compatible_with)
        if document_id:
            d = db.scalar(select(CreativeDocument).where(CreativeDocument.id == document_id, CreativeDocument.organization_id == ctx.org_id))
            if not d:
                raise TemplateError(404, "not_found", "Document not found")
            compat, only_compatible = (d.width, d.height), False       # annotate, don't hide: the UI shows incompatible ones as unsupported
        res = T.search(db, ctx, q=q, tab=tab, industry=industry, style=style, layout=layout, aspect=aspect, width=width, height=height, owner=owner, source=source,
                       compatible_with=compat if only_compatible else None, favorites=favorites, status=status, limit=limit, offset=offset)
        if compat and not only_compatible:
            for it in res["items"]:
                it["compatible"] = any((f["width"], f["height"]) == compat for f in it["supported_formats"])
        if thumbnails:
            T.attach_thumbnails(db, ctx, storage, res["items"], compat)
        return res
    return _guard(request, db, run)


@router.get("/templates/{tid}")
def get_template(tid: uuid.UUID, request: Request, version: int | None = None, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    def run():
        t = T.require_visible(db, ctx, tid)
        tv = T.version_of(db, t, version)
        return {**T.template_out(db, ctx, t, tv), "definition": tv.definition, "brand_constraints": tv.brand_constraints, "version_created_at": tv.created_at.isoformat(),
                "slots_by_format": {f["format_id"]: [{"id": s["id"], "role": s["role"], "kind": s["kind"], "required": s["required"]} for s in
                                                    (T.layout_for_size(db, tv, f["width"], f["height"]) or {"definition": {"slots": []}})["definition"]["slots"]] for f in T.supported_formats(db, tv)}}
    return _guard(request, db, run)


@router.get("/templates/{tid}/layouts/{format_id}/thumbnail")
def template_thumbnail(tid: uuid.UUID, format_id: str, request: Request, version: int | None = None, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        t = T.require_visible(db, ctx, tid)
        return T.thumbnail(db, ctx, storage, t, T.version_of(db, t, version), format_id)
    return _guard(request, db, run)


@router.get("/templates/{tid}/versions")
def template_versions(tid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _guard(request, db, lambda: T.versions(db, T.require_visible(db, ctx, tid)))


class TemplateIn(StrictIn):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    category: str = "general"
    style_tags: list[str] = Field(default_factory=list, max_length=7)
    layout_tags: list[str] = Field(default_factory=list, max_length=5)
    visibility: str = Field(default="personal", pattern="^(personal|organization)$")
    definition: dict = Field(default_factory=dict)
    layouts: dict[str, dict] = Field(min_length=1, max_length=40)
    brand_constraints: dict = Field(default_factory=dict)
    publish: bool = True


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        t = T.create_template(db, ctx, storage, name=body.name, description=body.description, category=body.category, style_tags=body.style_tags, layout_tags=body.layout_tags,
                              visibility=body.visibility, definition=body.definition, layouts=body.layouts, brand_constraints=body.brand_constraints, publish=body.publish)
        return T.template_out(db, ctx, t)
    r = _guard(request, db, run)
    return r


class PatchIn(StrictIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = None
    style_tags: list[str] | None = Field(default=None, max_length=7)
    layout_tags: list[str] | None = Field(default=None, max_length=5)
    visibility: str | None = Field(default=None, pattern="^(personal|organization)$")


@router.patch("/templates/{tid}")
def patch_template(tid: uuid.UUID, body: PatchIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db)):
    def run():
        t = T.require_visible(db, ctx, tid)
        ch = body.model_dump(exclude_unset=True)
        if "category" in ch and ch["category"] not in T.S.INDUSTRIES:
            raise TemplateError(422, "invalid_category", "Unknown industry")
        if any(x not in T.S.STYLES for x in ch.get("style_tags") or []) or any(x not in T.S.LAYOUTS for x in ch.get("layout_tags") or []):
            raise TemplateError(422, "invalid_tag", "Unknown style or layout tag")
        return T.template_out(db, ctx, T.update_meta(db, ctx, t, ch))
    return _guard(request, db, run)


class VersionIn(StrictIn):
    definition: dict = Field(default_factory=dict)
    layouts: dict[str, dict] = Field(min_length=1, max_length=40)
    brand_constraints: dict = Field(default_factory=dict)
    change_note: str = Field(default="", max_length=400)


@router.post("/templates/{tid}/versions", status_code=201)
def new_version(tid: uuid.UUID, body: VersionIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        t = T.require_visible(db, ctx, tid)
        T.add_version(db, ctx, storage, t, definition=body.definition, layouts=body.layouts, brand_constraints=body.brand_constraints, note=body.change_note)
        return T.template_out(db, ctx, t)
    return _guard(request, db, run)


@router.post("/templates/{tid}/publish")
def publish(tid: uuid.UUID, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db)):
    return _guard(request, db, lambda: T.template_out(db, ctx, T.set_status(db, ctx, T.require_visible(db, ctx, tid), "published")))


@router.post("/templates/{tid}/archive")
def archive(tid: uuid.UUID, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db)):
    return _guard(request, db, lambda: T.template_out(db, ctx, T.set_status(db, ctx, T.require_visible(db, ctx, tid), "archived")))


class DuplicateIn(StrictIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    visibility: str = Field(default="personal", pattern="^(personal|organization)$")


@router.post("/templates/{tid}/duplicate", status_code=201)
def duplicate(tid: uuid.UUID, body: DuplicateIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    return _guard(request, db, lambda: T.template_out(db, ctx, T.duplicate(db, ctx, storage, T.require_visible(db, ctx, tid), name=body.name, visibility=body.visibility)))


@router.get("/templates/{tid}/usage")
def template_usage(tid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _guard(request, db, lambda: T.usage(db, ctx, T.require_visible(db, ctx, tid)))


@router.post("/templates/{tid}/favorite")
def favorite(tid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    def run():
        T.set_favorite(db, ctx, T.require_visible(db, ctx, tid), True)
        return {"favorite": True}
    return _guard(request, db, run)


@router.delete("/templates/{tid}/favorite")
def unfavorite(tid: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    def run():
        T.set_favorite(db, ctx, T.require_visible(db, ctx, tid), False)
        return {"favorite": False}
    return _guard(request, db, run)


# ---------------------------------------------------------------- preview / apply
class PreviewIn(StrictIn):
    template_version: int | None = Field(default=None, ge=1)
    document_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    format_id: str | None = Field(default=None, pattern=r"^\d{2,4}x\d{2,4}$")
    mode: str = Field(pattern="^(layout_only|layout_style|full)$")
    confirm_replace: list[str] = Field(default_factory=list, max_length=12)
    slot_overrides: dict[str, str] = Field(default_factory=dict)


@router.post("/templates/{tid}/preview")
def preview(tid: uuid.UUID, body: PreviewIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    return _guard(request, db, lambda: A.preview(db, ctx, storage, tid, version=body.template_version, document_id=body.document_id, campaign_id=body.campaign_id,
                                                 format_id=body.format_id, mode=body.mode, confirm_replace=body.confirm_replace, slot_overrides=body.slot_overrides))


class ApplyIn(StrictIn):
    application_id: uuid.UUID
    expected_revision: int | None = Field(default=None, ge=0)
    confirm: bool = False


@router.post("/templates/{tid}/apply")
def apply(tid: uuid.UUID, body: ApplyIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """Commit a previewed candidate. Existing banner: through the editor command engine (APPLY_TEMPLATE; one undoable operation + version checkpoint).
    New banner: creates the creative + editable document. Rechecks the source revision first (409 revision_conflict)."""
    def run():
        app = A.get_application(db, ctx, body.application_id)
        if app.template_id != tid:
            raise TemplateError(404, "not_found", "Application not found")
        if app.document_id is None:
            doc = A.create_from_application(db, ctx, storage, app)
            return {"document": editor.view(db, ctx.org_id, doc, storage), "creative_id": str(doc.creative_id), "application_id": str(app.id), "created": True}
        doc, ops = A.apply_existing(db, ctx, storage, app, body.expected_revision, body.confirm)
        return {"document": editor.view(db, ctx.org_id, doc, storage), "application_id": str(app.id), "created": False, "operations": [{"id": str(o.id), "type": o.operation_type, "revision_after": o.revision_after} for o in ops]}
    return _guard(request, db, run)


@router.post("/template-applications/{aid}/reject")
def reject_application(aid: uuid.UUID, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db)):
    def run():
        a = A.reject(db, ctx, A.get_application(db, ctx, aid))
        return {"application_id": str(a.id), "status": a.status}
    return _guard(request, db, run)


class RevertIn(StrictIn):
    force: bool = False


@router.post("/template-applications/{aid}/revert")
def revert_application(aid: uuid.UUID, request: Request, body: RevertIn | None = None, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        app = A.get_application(db, ctx, aid)
        doc, ops = A.revert(db, ctx, storage, app, bool(body and body.force))
        return {"document": editor.view(db, ctx.org_id, doc, storage), "application_id": str(app.id), "status": app.status}
    return _guard(request, db, run)


@router.get("/creative-documents/{did}/template-applications")
def document_applications(did: uuid.UUID, request: Request, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return _guard(request, db, lambda: A.history(db, ctx, did))


# ---------------------------------------------------------------- save as template / copy layer
class SaveAsIn(StrictIn):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    category: str = "general"
    style_tags: list[str] = Field(default_factory=list, max_length=7)
    layout_tags: list[str] = Field(default_factory=list, max_length=5)
    visibility: str = Field(default="personal", pattern="^(personal|organization)$")
    formats: list[str] = Field(default_factory=list, max_length=30)
    include_background_assets: bool = False
    include_decorative_assets: bool = False
    preserve_brand_styling: bool = True
    new_version_of: uuid.UUID | None = None


@router.post("/creative-documents/{did}/save-as-template", status_code=201)
def save_as_template(did: uuid.UUID, body: SaveAsIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    def run():
        d = db.scalar(select(CreativeDocument).where(CreativeDocument.id == did, CreativeDocument.organization_id == ctx.org_id))
        if not d:
            raise TemplateError(404, "not_found", "Document not found")
        return T.save_as_template(db, ctx, storage, d, T.SaveOptions(name=body.name, description=body.description, category=body.category, style_tags=body.style_tags, layout_tags=body.layout_tags,
                                                                      visibility=body.visibility, formats=body.formats, include_background_assets=body.include_background_assets,
                                                                      include_decorative_assets=body.include_decorative_assets, preserve_brand_styling=body.preserve_brand_styling,
                                                                      new_version_of=body.new_version_of))
    return _guard(request, db, run)


class CopyLayerIn(StrictIn):
    source_document_id: uuid.UUID
    layer_id: str = Field(min_length=1, max_length=40)
    expected_revision: int = Field(ge=0)


@router.post("/creative-documents/{did}/copy-layer")
def copy_layer(did: uuid.UUID, body: CopyLayerIn, request: Request, ctx: OrgContext = DESIGNER, db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    """COPY_LAYER_TO_ARTBOARD: copy one layer from another banner of the same organization onto this artboard (scaled proportionally). The source is untouched."""
    def run():
        doc = editor.lock(db, ctx.org_id, did)
        c = OrgRepo(db, GeneratedCreative, ctx.org_id).get(doc.creative_id) if doc else None
        if not doc or not c:
            raise TemplateError(404, "not_found", "Document not found")
        ops, _ = A.copy_layer(db, ctx, doc, c, body.source_document_id, body.layer_id, body.expected_revision)
        return {"document": editor.view(db, ctx.org_id, doc, storage), "operations": [{"id": str(o.id), "type": o.operation_type, "revision_after": o.revision_after} for o in ops]}
    return _guard(request, db, run)
