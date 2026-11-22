import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.ai.image import ImageNotConfigured
from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.creative import formats
from app.export import service as export_service
from app.marketing.service import Conflict
from app.models import Campaign, CreativeConcept, ExportJob, FormatPreset, GeneratedCreative, GenerationJob, Role
from app.models.jobs import TERMINAL
from app.orchestration import factories, jobs
from app.repositories.org_repo import OrgRepo
from app.workers import tasks

router = APIRouter(tags=["orchestration"])
DEFAULT_SIZES = [(1080, 1080), (1200, 628), (1080, 1350)]


class SizeIn(BaseModel):
    width: int = Field(ge=formats.MIN_DIM, le=formats.MAX_DIM)
    height: int = Field(ge=formats.MIN_DIM, le=formats.MAX_DIM)


class GenerateIn(BaseModel):
    format_ids: list[str] = Field(default_factory=list, max_length=40)
    sizes: list[SizeIn] = Field(default_factory=list, max_length=20)   # custom dimensions
    preset_id: uuid.UUID | None = None
    concept_ids: list[uuid.UUID] | None = None
    product_asset_id: uuid.UUID | None = None
    regenerate_background: bool = False


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    format_ids: list[str] = Field(default_factory=list, max_length=40)
    custom_sizes: list[SizeIn] = Field(default_factory=list, max_length=20)


class ExportIn(BaseModel):
    creative_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    format: str = Field(default="png", pattern="^(png|jpeg|webp|svg)$")
    quality: int = Field(default=90, ge=40, le=100)
    quality_gate: str = Field(default="off", pattern="^(off|block_failed)$")
    transparent: bool = False

    @model_validator(mode="after")
    def _alpha(self):
        if self.transparent and self.format == "jpeg":
            raise ValueError("JPEG cannot be transparent; choose PNG, WebP or SVG")
        return self


def _job_out(j: GenerationJob) -> dict:
    return {"id": str(j.id), "kind": j.kind, "campaign_id": str(j.campaign_id), "status": j.status, "progress": j.progress, "stage_detail": j.stage_detail,
            "results": j.results, "error": j.error, "cost_usd": float(j.cost_usd or 0), "attempts": j.attempts, "cancel_requested": j.cancel_requested,
            "dead_lettered": j.dead_lettered, "error_status": j.params.get("error_status"), "planned_units": j.params.get("planned_units"),
            "created_at": j.created_at.isoformat() if j.created_at else None, "finished_at": j.finished_at.isoformat() if j.finished_at else None}


def _campaign(db, ctx, cid) -> Campaign:
    c = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


# ---- formats & presets
@router.get("/formats")
def list_formats(platform: str | None = None):
    r = formats.registry()
    return {"registry_version": r.registry_version, "note": r.note, "platforms": formats.PLATFORMS,
            "formats": [f.model_dump() for f in r.formats if not platform or f.platform == platform],
            "custom_limits": {"min": formats.MIN_DIM, "max": formats.MAX_DIM}}


@router.get("/format-presets")
def list_presets(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return [{"id": str(p.id), "name": p.name, "format_ids": p.format_ids, "custom_sizes": p.custom_sizes} for p in OrgRepo(db, FormatPreset, ctx.org_id).list()]


@router.post("/format-presets", status_code=201)
def create_preset(body: PresetIn, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    bad = [f for f in body.format_ids if not formats.get(f)]
    if bad or not (body.format_ids or body.custom_sizes):
        raise HTTPException(422, f"Unknown formats {bad}" if bad else "Preset needs at least one format or size")
    p = OrgRepo(db, FormatPreset, ctx.org_id).add(name=body.name, format_ids=body.format_ids, custom_sizes=[s.model_dump() for s in body.custom_sizes])
    db.commit()
    return {"id": str(p.id), "name": p.name, "format_ids": p.format_ids, "custom_sizes": p.custom_sizes}


@router.delete("/format-presets/{pid}", status_code=204)
def delete_preset(pid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    repo = OrgRepo(db, FormatPreset, ctx.org_id)
    p = repo.get(pid)
    if not p:
        raise HTTPException(404, "Preset not found")
    repo.soft_delete(p)
    db.commit()


# ---- generation jobs
@router.post("/campaigns/{cid}/creatives/generate", status_code=202)
def generate(cid: uuid.UUID, body: GenerateIn, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db),
             idempotency_key: str | None = Header(default=None, max_length=64)):
    camp = _campaign(db, ctx, cid)
    fids, custom = list(body.format_ids), [(s.width, s.height) for s in body.sizes]
    if body.preset_id:
        p = OrgRepo(db, FormatPreset, ctx.org_id).get(body.preset_id)
        if not p:
            raise HTTPException(404, "Preset not found")
        fids += p.format_ids
        custom += [(s["width"], s["height"]) for s in p.custom_sizes]
    if not fids and not custom:
        custom = DEFAULT_SIZES
    try:
        sizes = jobs.resolve_sizes(fids, custom)
        job, created = jobs.create_job(db, ctx.org_id, camp, sizes, body.product_asset_id, body.regenerate_background, body.concept_ids, idempotency_key)
    except (jobs.BadRequest,) as e:
        raise HTTPException(422, str(e))
    except Conflict as e:
        raise HTTPException(409, str(e))
    except ImageNotConfigured as e:
        raise HTTPException(503, str(e))
    db.commit()  # the worker must be able to see the job
    if created:
        tasks.run_banner_job.delay(str(ctx.org_id), str(job.id))
        db.refresh(job)
    return JSONResponse(_job_out(job), status_code=202 if created else 200)


@router.get("/jobs/{jid}")
def get_job(jid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    j = OrgRepo(db, GenerationJob, ctx.org_id).get(jid)
    if not j:
        raise HTTPException(404, "Job not found")
    return _job_out(j)


@router.get("/campaigns/{cid}/jobs")
def list_jobs(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    _campaign(db, ctx, cid)
    return [_job_out(j) for j in OrgRepo(db, GenerationJob, ctx.org_id).list(campaign_id=cid)]


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    j = OrgRepo(db, GenerationJob, ctx.org_id).get(jid)
    if not j:
        raise HTTPException(404, "Job not found")
    if j.status in TERMINAL:
        raise HTTPException(409, f"Job already {j.status}")
    j.cancel_requested = True
    if j.status == "pending":  # not picked up yet: cancel immediately (the worker will also see the flag)
        j.status = "cancelled"
    db.commit()
    return _job_out(j)


@router.post("/creatives/{crid}/regenerate", status_code=202)
def regenerate(crid: uuid.UUID, new_background: bool = Query(False), ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    cr = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not cr:
        raise HTTPException(404, "Creative not found")
    camp = _campaign(db, ctx, cr.campaign_id)
    try:
        job, created = jobs.create_job(db, ctx.org_id, camp, [jobs.SizeReq(cr.width, cr.height, cr.format_keys or [])], None, new_background, [cr.concept_id])
    except Conflict as e:
        raise HTTPException(409, str(e))
    except ImageNotConfigured as e:
        raise HTTPException(503, str(e))
    db.commit()
    tasks.run_banner_job.delay(str(ctx.org_id), str(job.id))
    db.refresh(job)
    return JSONResponse(_job_out(job), status_code=202)


# ---- export
@router.post("/campaigns/{cid}/exports", status_code=202)
def create_export(cid: uuid.UUID, body: ExportIn, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    ctx.require(Role.viewer)
    _campaign(db, ctx, cid)
    if body.creative_ids:
        owned = {c.id for c in OrgRepo(db, GeneratedCreative, ctx.org_id).list(campaign_id=cid)}
        if any(i not in owned for i in body.creative_ids):
            raise HTTPException(404, "Creative not found in this campaign")
    e = OrgRepo(db, ExportJob, ctx.org_id).add(campaign_id=cid, params={"creative_ids": [str(i) for i in body.creative_ids or []], "format": body.format, "quality": body.quality, "quality_gate": body.quality_gate, "transparent": body.transparent})
    db.commit()
    tasks.run_export_job.delay(str(ctx.org_id), str(e.id))
    db.refresh(e)
    return JSONResponse(_export_out(e), status_code=202)


def _export_out(e: ExportJob) -> dict:
    url = None
    if e.status == "completed" and e.storage_key:
        url = factories.make_storage().signed_url(e.storage_key)
    return {"id": str(e.id), "campaign_id": str(e.campaign_id), "status": e.status, "file_count": e.file_count, "size_bytes": e.size_bytes,
            "error": e.error, "url": url, "manifest": e.manifest if e.status == "completed" else None, "params": e.params}


@router.get("/exports/{eid}")
def get_export(eid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    e = OrgRepo(db, ExportJob, ctx.org_id).get(eid)
    if not e:
        raise HTTPException(404, "Export not found")
    return _export_out(e)


@router.get("/creatives/{crid}/download")
def download(crid: uuid.UUID, format: str = Query("png", pattern="^(png|jpeg|webp|svg)$"), quality: int = Query(90, ge=40, le=100),
             ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    cr = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not cr or not cr.render_asset_id:
        raise HTTPException(404, "Creative not found")
    fmts = [formats.get(k) for k in cr.format_keys or []]
    limits = [f.max_file_kb for f in fmts if f and f.max_file_kb]
    try:
        enc = export_service.encode_creative(db, ctx.org_id, cr, factories.make_storage(), format, quality, min(limits) if limits else None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    concept = OrgRepo(db, CreativeConcept, ctx.org_id).get(cr.concept_id)
    name = f"{export_service.slug(concept.data['name'] if concept else 'banner')}_{cr.width}x{cr.height}.{enc.ext}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"}
    if enc.warnings:
        headers["X-Export-Warnings"] = re.sub(r"[^\x20-\x7e]", "", "; ".join(enc.warnings))[:400]
    return Response(enc.data, media_type=enc.mime, headers=headers)
