import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.llm import LLMError, LLMNotConfigured
from app.api.jobs import _job_out
from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.marketing import service
from app.marketing.schemas import GenerateConceptsIn
from app.models import Campaign, CreativeBrief, CreativeConcept, Product, Role, UsageRecord
from app.orchestration import factories, textjobs
from app.repositories.org_repo import OrgRepo
from app.workers import tasks

router = APIRouter(tags=["marketing-intelligence"])


class ConceptOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    approach: str
    data: dict
    flags: list
    status: str
    selected: bool
    edited: bool
    prompt_version: str
    provider: str
    model: str


class ConceptEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    headline: str | None = Field(default=None, min_length=1, max_length=80)
    alternative_headlines: list[str] | None = Field(default=None, max_length=5)
    supporting_copy: str | None = Field(default=None, max_length=220)
    cta: str | None = Field(default=None, min_length=1, max_length=30)
    visual_direction: str | None = Field(default=None, max_length=500)
    background_description: str | None = Field(default=None, max_length=500)
    acknowledge_flags: bool = False  # user takes responsibility for flagged claims


class SelectIn(BaseModel):
    concept_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)


def _campaign(db: Session, ctx: OrgContext, cid: uuid.UUID) -> Campaign:
    c = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


def _llm_errors(fn):
    try:
        return fn()
    except LLMNotConfigured as e:
        raise HTTPException(503, str(e))
    except LLMError as e:
        raise HTTPException(502, f"Language model call failed: {e}")
    except service.Conflict as e:
        raise HTTPException(409, str(e))


def _queue(db: Session, ctx: OrgContext, c: Campaign, kind: str, params: dict | None = None) -> JSONResponse:
    try:
        job = textjobs.create(db, ctx.org_id, c, kind, params)
    except service.Conflict as e:
        raise HTTPException(409, str(e))
    db.commit()  # the worker must be able to see the job
    tasks.run_text_job.delay(str(ctx.org_id), str(job.id))
    db.refresh(job)
    return JSONResponse(_job_out(job), status_code=202)


def _preflight(db: Session, ctx: OrgContext, c: Campaign) -> None:
    """Fast, deterministic failures stay synchronous: missing analysis (409) and an unconfigured provider (503)."""
    if not OrgRepo(db, Product, ctx.org_id).list(campaign_id=c.id):
        raise HTTPException(409, "Analyze the product first")
    _llm_errors(factories.make_llm_provider)


@router.post("/campaigns/{cid}/concepts/generate", status_code=202)
def generate(cid: uuid.UUID, body: GenerateConceptsIn, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):
    """Queues concept generation. Poll GET /jobs/{id}, then GET /campaigns/{cid}/concepts."""
    c = _campaign(db, ctx, cid)
    _preflight(db, ctx, c)
    return _queue(db, ctx, c, "concepts", {"count": body.count, "approaches": [a.value for a in body.approaches] if body.approaches else None})


@router.get("/campaigns/{cid}/concepts", response_model=list[ConceptOut])
def list_concepts(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    _campaign(db, ctx, cid)
    return list(reversed(OrgRepo(db, CreativeConcept, ctx.org_id).list(campaign_id=cid)))


@router.patch("/concepts/{conc_id}", response_model=ConceptOut)
def edit_concept(conc_id: uuid.UUID, body: ConceptEdit, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):
    conc = OrgRepo(db, CreativeConcept, ctx.org_id).get(conc_id)
    if not conc:
        raise HTTPException(404, "Concept not found")
    camp = _campaign(db, ctx, conc.campaign_id)
    changes = body.model_dump(exclude_unset=True, exclude={"acknowledge_flags"})
    conc.data = {**conc.data, **changes}
    conc.edited = True
    _llm_errors(lambda: service.recheck_concept(db, ctx.org_id, camp, conc))
    if conc.flags and body.acknowledge_flags:
        conc.status = "acknowledged"
    db.commit()
    return conc


@router.post("/campaigns/{cid}/concepts/select", response_model=list[ConceptOut])
def select_concepts(cid: uuid.UUID, body: SelectIn, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):
    _campaign(db, ctx, cid)
    repo = OrgRepo(db, CreativeConcept, ctx.org_id)
    all_c = {c.id: c for c in repo.list(campaign_id=cid)}
    chosen = []
    for i in body.concept_ids:
        if i not in all_c:
            raise HTTPException(404, f"Concept {i} not found in this campaign")
        if all_c[i].status == "flagged":
            raise HTTPException(409, "Concept has unresolved unsupported claims: edit the copy or acknowledge the flags")
        chosen.append(all_c[i])
    for c in all_c.values():
        c.selected = c.id in set(body.concept_ids)
    db.commit()
    return chosen


@router.post("/campaigns/{cid}/briefs/generate", status_code=202)
def make_briefs(cid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):
    c = _campaign(db, ctx, cid)
    _preflight(db, ctx, c)
    if not any(x.selected for x in OrgRepo(db, CreativeConcept, ctx.org_id).list(campaign_id=c.id)):
        raise HTTPException(409, "Select at least one concept first")
    return _queue(db, ctx, c, "briefs")


@router.get("/campaigns/{cid}/briefs")
def list_briefs(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    _campaign(db, ctx, cid)
    ids = {c.id for c in OrgRepo(db, CreativeConcept, ctx.org_id).list(campaign_id=cid)}
    return [{"id": str(b.id), "concept_id": str(b.concept_id), "data": b.data, "prompt_version": b.prompt_version}
            for b in OrgRepo(db, CreativeBrief, ctx.org_id).list() if b.concept_id in ids]


@router.get("/campaigns/{cid}/usage")
def usage(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    _campaign(db, ctx, cid)
    q = select(UsageRecord.kind, func.count(), func.sum(UsageRecord.input_tokens), func.sum(UsageRecord.output_tokens),
               func.sum(UsageRecord.cost_usd), func.bool_and(UsageRecord.priced)).where(
        UsageRecord.organization_id == ctx.org_id, UsageRecord.campaign_id == cid).group_by(UsageRecord.kind)
    rows = [{"kind": k, "calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0), "cost_usd": float(c or 0), "priced": bool(p)}
            for k, n, i, o, c, p in db.execute(q)]
    return {"by_kind": rows, "total_cost_usd": sum(r["cost_usd"] for r in rows), "fully_priced": all(r["priced"] for r in rows) if rows else True}
