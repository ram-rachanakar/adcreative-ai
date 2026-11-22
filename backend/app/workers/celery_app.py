from celery import Celery

from app.core.config import get_settings

s = get_settings()
celery_app = Celery("adcreative", broker=s.redis_url, backend=s.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_always_eager=s.celery_eager, task_eager_propagates=False, broker_connection_retry_on_startup=True,
    task_default_queue="adcreative", result_expires=3600,
)
