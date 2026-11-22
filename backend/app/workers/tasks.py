import uuid

from app.core.db import SessionLocal, set_org
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="adcreative.run_banner_job", max_retries=2, acks_late=True)
def run_banner_job(self, org_id: str, job_id: str) -> None:
    from app.orchestration import jobs
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    with SessionLocal() as db:
        set_org(db, org)  # org id comes from our own DB row, written after membership checks — never from the browser
        try:
            jobs.run_job(db, org, jid)
        except Exception as e:
            from app.models import GenerationJob
            from app.repositories.org_repo import OrgRepo
            db.rollback()
            if self.request.retries < self.max_retries:
                j = OrgRepo(db, GenerationJob, org).get(jid)
                if j:
                    j.attempts += 1
                    db.commit()
                raise self.retry(exc=e, countdown=5 * 2 ** self.request.retries)  # exponential backoff
            jobs.mark_dead_letter(db, org, jid, f"{type(e).__name__}: {e}")


@celery_app.task(bind=True, name="adcreative.run_export_job", max_retries=1, acks_late=True)
def run_export_job(self, org_id: str, export_id: str) -> None:
    from app.export import service
    org, eid = uuid.UUID(org_id), uuid.UUID(export_id)
    with SessionLocal() as db:
        set_org(db, org)
        service.run_export(db, org, eid)


@celery_app.task(bind=True, name="adcreative.run_quality_job", max_retries=1, acks_late=True)
def run_quality_job(self, org_id: str, job_id: str) -> None:
    from app.orchestration import jobs
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    with SessionLocal() as db:
        set_org(db, org)
        try:
            jobs.run_quality_job(db, org, jid)
        except Exception as e:
            db.rollback()
            jobs.mark_dead_letter(db, org, jid, f"{type(e).__name__}: {e}")


@celery_app.task(bind=True, name="adcreative.run_render_job", max_retries=1, acks_late=True)
def run_render_job(self, org_id: str, job_id: str) -> None:
    from app.editor import service
    from app.orchestration import factories
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    with SessionLocal() as db:
        set_org(db, org)
        service.run_render_job(db, org, jid, factories.make_storage())


@celery_app.task(bind=True, name="adcreative.run_regeneration_job", max_retries=0, acks_late=True)
def run_regeneration_job(self, org_id: str, job_id: str) -> None:
    from app.editor import regeneration
    from app.orchestration import factories
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    with SessionLocal() as db:
        set_org(db, org)
        try:
            image = factories.make_image_provider()
        except Exception as e:  # noqa: BLE001 - e.g. provider not configured: surfaced as a failed job, not a crash
            from app.models import RegenerationJob
            from app.repositories.org_repo import OrgRepo
            j = OrgRepo(db, RegenerationJob, org).get(jid)
            if j:
                j.status, j.error_message = "failed", f"{type(e).__name__}: {e}"[:500]
                db.commit()
            return
        regeneration.run_job(db, org, jid, factories.make_storage(), image, factories.make_llm_provider())


@celery_app.task(bind=True, name="adcreative.run_text_job", max_retries=0, acks_late=True)
def run_text_job(self, org_id: str, job_id: str) -> None:
    from app.orchestration import textjobs
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    with SessionLocal() as db:
        set_org(db, org)
        textjobs.run(db, org, jid)
