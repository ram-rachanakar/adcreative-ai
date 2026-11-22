import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.api.jobs import _job_out
from app.core.db import get_db
from app.marketing.service import Conflict
from app.models import Campaign, GeneratedCreative, Organization, QualityReport, Role
from app.orchestration import factories
from app.quality import service
from app.quality.policy import QualityPolicy, resolve_policy
from app.repositories.org_repo import OrgRepo
from app.workers import tasks

router = APIRouter(tags=["quality"])


def _creative(db, ctx, crid) -> GeneratedCreative:
    c = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not c:
        raise HTTPException(404, "Creative not found")
    return c


def _report_out(row: QualityReport, cr: GeneratedCreative) -> dict:
    return {"id": str(row.id), "creative_id": str(row.creative_id), "created_at": row.created_at.isoformat() if row.created_at else None,
            "spec_version": row.spec_version, "stale": row.spec_version != cr.spec_version, "can_revert": bool(row.original_spec) and row.spec_version == cr.spec_version,
            **{k: v for k, v in row.report.items() if k != "policy"}}


@router.get("/quality/policy")
def get_policy(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    org = db.get(Organization, ctx.org_id)
    return {"overrides": org.quality_policy or {}, "effective": resolve_policy(org.quality_policy).model_dump(), "defaults": QualityPolicy().model_dump()}


@router.put("/quality/policy")
def put_policy(body: dict, ctx: OrgContext = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    try:
        resolve_policy(body)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    org = db.get(Organization, ctx.org_id)
    org.quality_policy = {k: v for k, v in body.items() if v is not None}
    db.commit()
    return {"overrides": org.quality_policy, "effective": resolve_policy(org.quality_policy).model_dump()}


class RunIn(BaseModel):
    vision: bool | None = None
    auto_correct: bool = False          # a manual run never rewrites a banner unless asked (the editor's work is the user's)
    policy: dict | None = None


@router.get("/creatives/{crid}/quality")
def get_quality(crid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    cr = _creative(db, ctx, crid)
    rows = OrgRepo(db, QualityReport, ctx.org_id).list(creative_id=crid)
    if not rows:
        return {"assessed": False, "creative_id": str(crid), "history": []}
    return {"assessed": True, **_report_out(rows[0], cr),
            "history": [{"id": str(r.id), "created_at": r.created_at.isoformat() if r.created_at else None, "score": r.score, "status": r.status, "spec_version": r.spec_version} for r in rows[:20]]}


@router.post("/creatives/{crid}/quality")
def run_quality(crid: uuid.UUID, body: RunIn, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    cr = _creative(db, ctx, crid)
    try:
        pol = service.org_policy(db, ctx.org_id, body.policy)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    use_vision = pol.vision_enabled if body.vision is None else body.vision
    row = service.assess_creative(db, ctx.org_id, cr, factories.make_storage(), pol, llm=factories.make_llm_provider() if use_vision else None,
                                  use_vision=use_vision, auto_correct=body.auto_correct)
    db.commit()
    return _report_out(row, cr)


@router.post("/creatives/{crid}/quality/revert")
def revert(crid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    cr = _creative(db, ctx, crid)
    try:
        service.revert_corrections(db, ctx.org_id, cr, factories.make_storage())
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"id": str(cr.id), "spec_version": cr.spec_version, "quality_status": cr.quality_status, "stale": True}


class CampaignRunIn(RunIn):
    creative_ids: list[uuid.UUID] | None = None


@router.post("/campaigns/{cid}/quality", status_code=202)
def run_campaign_quality(cid: uuid.UUID, body: CampaignRunIn, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    from app.orchestration import jobs
    camp = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not camp:
        raise HTTPException(404, "Campaign not found")
    try:
        job = jobs.create_quality_job(db, ctx.org_id, camp, body.creative_ids, body.vision, body.auto_correct, body.policy)
    except Conflict as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    db.commit()
    tasks.run_quality_job.delay(str(ctx.org_id), str(job.id))
    db.refresh(job)
    return JSONResponse(_job_out(job), status_code=202)


@router.get("/campaigns/{cid}/quality")
def campaign_summary(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    if not OrgRepo(db, Campaign, ctx.org_id).get(cid):
        raise HTTPException(404, "Campaign not found")
    crs = OrgRepo(db, GeneratedCreative, ctx.org_id).list(campaign_id=cid)
    counts = {"pass": 0, "warn": 0, "fail": 0, "unassessed": 0}
    scores, stale = [], 0
    for c in crs:
        counts[c.quality_status or "unassessed"] += 1
        if c.quality_score is not None:
            scores.append(c.quality_score)
            stale += c.quality_spec_version != c.spec_version
    return {"total": len(crs), "counts": counts, "average_score": round(sum(scores) / len(scores), 1) if scores else None, "stale": stale}
