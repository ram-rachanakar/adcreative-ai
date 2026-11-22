"""Async execution of the request-time pipeline stages (product analysis, concept generation, brief generation).
They reuse `generation_jobs` (kind = analyze | concepts | briefs) so the UI polls one job API. Cheap, deterministic validation
happens in the endpoint (fast 4xx); everything that can be slow or flaky (fetching pages, LLM calls) runs in the worker."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.llm import LLMError, LLMNotConfigured
from app.marketing import service as marketing
from app.marketing.schemas import Approach
from app.marketing.service import Conflict
from app.models import Campaign, GenerationJob
from app.models.jobs import TERMINAL
from app.orchestration import factories
from app.repositories.org_repo import OrgRepo
from app.scraping import service as scraping
from app.scraping.fetcher import FetchError, UnsafeURLError

KINDS = {"analyze": "analyzing", "concepts": "generating_assets", "briefs": "briefing"}


def create(db: Session, org_id: uuid.UUID, campaign: Campaign, kind: str, params: dict | None = None) -> GenerationJob:
    repo = OrgRepo(db, GenerationJob, org_id)
    if any(j.status not in TERMINAL for j in repo.list(campaign_id=campaign.id)):
        raise Conflict("Another job is already running for this campaign")
    job = repo.add(campaign_id=campaign.id, kind=kind, status="pending", idempotency_key=uuid.uuid4().hex, params={**(params or {}), "planned_units": 1})
    db.flush()
    return job


def _fail(db: Session, org_id: uuid.UUID, job_id: uuid.UUID, status: int, msg: str, dead: bool = False) -> None:
    db.rollback()
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    if job and job.status not in TERMINAL:
        job.status, job.error, job.finished_at, job.dead_lettered = "failed", msg[:500], datetime.now(timezone.utc), dead
        job.params = {**job.params, "error_status": status}   # the HTTP status the old synchronous endpoint would have returned
        db.commit()


def run(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> None:
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    if not job or job.status in TERMINAL:      # also makes broker redelivery safe
        return
    if job.cancel_requested:
        job.status, job.finished_at = "cancelled", datetime.now(timezone.utc)
        db.commit()
        return
    job.status, job.started_at, job.error, job.progress = KINDS[job.kind], job.started_at or datetime.now(timezone.utc), None, 10
    db.commit()
    camp = OrgRepo(db, Campaign, org_id).get(job.campaign_id)
    try:
        if camp is None:
            raise Conflict("Campaign no longer exists")
        p = job.params
        if job.kind == "analyze":
            prod = scraping.analyze_campaign(db, org_id, camp, scraping.make_fetcher(), factories.make_storage())
            results = [{"unit": "product", "status": "ok", "product_id": str(prod.id)}]
        elif job.kind == "concepts":
            rows = marketing.generate_concepts(db, org_id, camp, factories.make_llm_provider(), p.get("count", 5),
                                              [Approach(a) for a in p["approaches"]] if p.get("approaches") else None)
            results = [{"unit": str(r.id), "status": "ok", "concept_id": str(r.id)} for r in rows]
        else:
            rows = marketing.generate_briefs(db, org_id, camp, factories.make_llm_provider())
            results = [{"unit": str(r.id), "status": "ok", "brief_id": str(r.id)} for r in rows]
        job = OrgRepo(db, GenerationJob, org_id).get(job_id)
        job.results, job.status, job.progress, job.finished_at = results, "completed", 100, datetime.now(timezone.utc)
        db.commit()
    except UnsafeURLError as e:
        _fail(db, org_id, job_id, 400, f"URL not allowed: {e}")
    except FetchError as e:
        _fail(db, org_id, job_id, 422, f"Could not fetch product page: {e}")
    except ValueError as e:
        _fail(db, org_id, job_id, 422, str(e))
    except LLMNotConfigured as e:
        _fail(db, org_id, job_id, 503, str(e))
    except LLMError as e:
        _fail(db, org_id, job_id, 502, f"Language model call failed: {e}")
    except Conflict as e:
        _fail(db, org_id, job_id, 409, str(e))
    except Exception as e:  # noqa: BLE001 - never leave a job stuck non-terminal (it would block the campaign)
        _fail(db, org_id, job_id, 500, f"{type(e).__name__}: {e}", dead=True)
