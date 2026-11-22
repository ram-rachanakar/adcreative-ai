"""Job creation (validation, concurrency guard, idempotency) and the resumable runner executed by the Celery worker."""
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.image import ImageNotConfigured
from app.creative import formats
from app.creative.service import SizeReq, generate_creatives, prepare
from app.marketing.service import Conflict
from app.models import Campaign, GenerationJob
from app.models.jobs import TERMINAL
from app.orchestration import factories
from app.repositories.org_repo import OrgRepo


class BadRequest(Exception):
    pass


def resolve_sizes(format_ids: list[str], custom: list[tuple[int, int]]) -> list[SizeReq]:
    """Merge registry formats and custom dimensions into unique sizes, remembering which placements each size serves."""
    by_size: dict[tuple[int, int], SizeReq] = {}
    for fid in dict.fromkeys(format_ids):
        f = formats.get(fid)
        if not f:
            raise BadRequest(f"Unknown format '{fid}'")
        by_size.setdefault((f.width, f.height), SizeReq(f.width, f.height, [])).format_keys.append(f.id)
    for w, h in custom:
        if not formats.valid_custom(w, h):
            raise BadRequest(f"Custom size {w}x{h} must be between {formats.MIN_DIM} and {formats.MAX_DIM}px")
        sr = by_size.setdefault((w, h), SizeReq(w, h, []))
        if not sr.format_keys:
            sr.format_keys = [f.id for f in formats.for_size(w, h)]  # a custom size that matches a registry size inherits its placements
    return list(by_size.values())


def create_job(db: Session, org_id: uuid.UUID, campaign: Campaign, sizes: list[SizeReq], product_asset_id=None, regenerate=False,
               concept_ids=None, explicit_key: str | None = None) -> tuple[GenerationJob, bool]:
    prep = prepare(db, org_id, campaign, sizes, product_asset_id, concept_ids)  # raises Conflict before anything is queued or paid
    factories.make_image_provider()                                      # raises ImageNotConfigured up front
    repo = OrgRepo(db, GenerationJob, org_id)
    if explicit_key:
        for j in repo.list(campaign_id=campaign.id):
            if j.idempotency_key == explicit_key:
                return j, False
    if any(j.status not in TERMINAL for j in repo.list(campaign_id=campaign.id)):
        raise Conflict("A generation job is already running for this campaign")
    job = repo.add(campaign_id=campaign.id, status="pending", idempotency_key=explicit_key or uuid.uuid4().hex,
                   params={"sizes": [{"width": s.width, "height": s.height, "format_keys": s.format_keys} for s in sizes],
                           "product_asset_id": str(product_asset_id) if product_asset_id else None, "regenerate_background": regenerate,
                           "concept_ids": [str(c) for c in concept_ids] if concept_ids else None,
                           "planned_units": len(prep.concepts) * len(sizes)})
    db.flush()
    return job, True


def _finish(db: Session, job: GenerationJob, status: str, error: str | None = None) -> None:
    job.status, job.error, job.finished_at = status, error, datetime.now(timezone.utc)
    if status in ("completed", "partially_completed"):
        job.progress = 100
    db.commit()


sleep_fn = time.sleep  # module-level so tests (and ops) can replace backoff waiting


def run_job(db: Session, org_id: uuid.UUID, job_id: uuid.UUID, retry_sleep=None) -> None:
    """Idempotent: safe to call again after a crash/redelivery; completed units are skipped, failed ones are retried."""
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    if not job or job.status in TERMINAL:
        return
    campaign = OrgRepo(db, Campaign, org_id).get(job.campaign_id)
    if job.cancel_requested:
        return _finish(db, job, "cancelled")
    job.results = [r for r in job.results if r.get("status") == "ok"]
    job.started_at = job.started_at or datetime.now(timezone.utc)
    job.status, job.error = "generating_assets", None
    db.commit()

    def on_stage(stage, detail, pct):
        job.status, job.stage_detail, job.progress = stage, detail, max(job.progress, min(pct, 99))
        db.commit()

    def should_cancel():
        db.refresh(job)
        return job.cancel_requested

    def on_unit(result):
        job.results = [*job.results, result]
        db.commit()

    from app.quality import service as quality
    pol = quality.org_policy(db, org_id)

    def on_quality(cr):
        llm = factories.make_llm_provider() if pol.vision_enabled else None
        row = quality.assess_creative(db, org_id, cr, factories.make_storage(), pol, llm=llm)
        return {"quality_score": row.score, "quality_status": row.status, "quality_corrections": sum(len(r["actions"]) for r in row.report["corrections"] if r["accepted"])}

    p = job.params
    sizes = [SizeReq(s["width"], s["height"], s["format_keys"]) for s in p["sizes"]]
    try:
        out = generate_creatives(db, org_id, campaign, factories.make_image_provider(), factories.make_storage(), sizes,
                                 uuid.UUID(p["product_asset_id"]) if p.get("product_asset_id") else None, p.get("regenerate_background", False),
                                 concept_ids=[uuid.UUID(c) for c in p["concept_ids"]] if p.get("concept_ids") else None, job_id=job.id,
                                 done={r["unit"] for r in job.results}, on_stage=on_stage, should_cancel=should_cancel, on_unit=on_unit,
                                 retry_sleep=retry_sleep or (lambda sec: sleep_fn(sec)), on_quality=on_quality)
    except (Conflict, ImageNotConfigured) as e:  # permanent errors: retrying cannot help
        db.rollback()
        job = OrgRepo(db, GenerationJob, org_id).get(job_id)
        return _finish(db, job, "failed", str(e))
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    job.cost_usd = float(job.cost_usd or 0) + out["cost"]
    if out["cancelled"]:
        return _finish(db, job, "cancelled")
    ok = sum(1 for r in job.results if r["status"] == "ok")
    bad = sum(1 for r in job.results if r["status"] == "failed")
    if ok and not bad:
        _finish(db, job, "completed")
    elif ok:
        _finish(db, job, "partially_completed", f"{bad} of {ok + bad} banners failed")
    else:
        _finish(db, job, "failed", "; ".join(sorted({r.get("error") or "unknown" for r in job.results}))[:500] or "No banners were produced")


def mark_dead_letter(db: Session, org_id: uuid.UUID, job_id: uuid.UUID, error: str) -> None:
    db.rollback()
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    if job and job.status not in TERMINAL:
        job.dead_lettered = True
        _finish(db, job, "failed", f"Gave up after repeated worker errors: {error[:300]}")


# ---- quality jobs (re-assess existing banners; optional vision pass; optional bounded auto-correction)
def create_quality_job(db: Session, org_id: uuid.UUID, campaign: Campaign, creative_ids: list[uuid.UUID] | None, vision: bool | None,
                       auto_correct: bool | None, policy: dict | None) -> GenerationJob:
    from app.models import GeneratedCreative
    from app.quality import service as quality
    repo = OrgRepo(db, GenerationJob, org_id)
    if any(j.status not in TERMINAL for j in repo.list(campaign_id=campaign.id)):
        raise Conflict("A job is already running for this campaign")
    rows = [c for c in OrgRepo(db, GeneratedCreative, org_id).list(campaign_id=campaign.id) if c.render_asset_id and (not creative_ids or c.id in creative_ids)]
    if not rows:
        raise Conflict("No banners to assess")
    quality.org_policy(db, org_id, policy)  # validates overrides before anything is queued
    job = repo.add(campaign_id=campaign.id, kind="quality", status="pending", idempotency_key=uuid.uuid4().hex,
                   params={"creative_ids": [str(c.id) for c in rows], "vision": vision, "auto_correct": auto_correct, "policy": policy or {}, "planned_units": len(rows)})
    db.flush()
    return job


def run_quality_job(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> None:
    from app.models import GeneratedCreative
    from app.quality import service as quality
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    if not job or job.status in TERMINAL:
        return
    if job.cancel_requested:
        return _finish(db, job, "cancelled")
    job.results = [r for r in job.results if r.get("status") == "ok"]
    job.started_at = job.started_at or datetime.now(timezone.utc)
    job.status, job.error = "validating", None
    db.commit()
    p = job.params
    pol = quality.org_policy(db, org_id, p.get("policy"))
    want_vision = pol.vision_enabled if p.get("vision") is None else p["vision"]
    llm = factories.make_llm_provider() if want_vision else None
    storage = factories.make_storage()
    ids = [uuid.UUID(i) for i in p["creative_ids"]]
    done = {r["unit"] for r in job.results}
    for n, cid in enumerate(ids):
        if str(cid) in done:
            continue
        db.refresh(job)
        if job.cancel_requested:
            return _finish(db, job, "cancelled")
        cr = OrgRepo(db, GeneratedCreative, org_id).get(cid)
        if cr is None:
            res = {"unit": str(cid), "status": "failed", "error": "Banner no longer exists"}
        else:
            try:
                row = quality.assess_creative(db, org_id, cr, storage, pol, llm=llm, use_vision=want_vision, auto_correct=p.get("auto_correct"))
                res = {"unit": str(cid), "creative_id": str(cid), "status": "ok", "quality_score": row.score, "quality_status": row.status,
                       "quality_corrections": sum(len(r["actions"]) for r in row.report["corrections"] if r["accepted"])}
            except Exception as e:
                db.rollback()
                res = {"unit": str(cid), "creative_id": str(cid), "status": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        job = OrgRepo(db, GenerationJob, org_id).get(job_id)
        job.results = [*job.results, res]
        job.progress, job.stage_detail = min(99, int(100 * (n + 1) / len(ids))), f"Assessed {n + 1}/{len(ids)}"
        db.commit()
    job = OrgRepo(db, GenerationJob, org_id).get(job_id)
    bad = sum(1 for r in job.results if r["status"] == "failed")
    if bad == len(job.results):
        _finish(db, job, "failed", "; ".join(sorted({r.get("error") or "unknown" for r in job.results}))[:500])
    else:
        _finish(db, job, "partially_completed" if bad else "completed", f"{bad} banner(s) could not be assessed" if bad else None)
