"""Celery entry points matching the existing FastAPI handoff."""

import logging
from random import uniform
from shutil import which

from celery import Celery, signals
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import sync_engine
from worker.config import worker_settings
from worker.pipeline import generate, queue_expired
from worker.state import (
    TASK_SECONDS,
    circuit_is_open,
    generation_slot,
    load_job,
    stale_work_ids,
    work_lock,
)

QUEUE = "i2v_generate"
celery_app = Celery("i2v", broker=get_settings().redis_url)
celery_app.conf.update(
    task_default_queue=QUEUE,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=TASK_SECONDS,
    task_time_limit=TASK_SECONDS + 45,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "reconcile-video-works": {"task": "i2v.reconcile_stale", "schedule": 60.0},
    },
)

logger = logging.getLogger(__name__)


@signals.worker_init.connect
def validate_worker(**_kwargs) -> None:
    options = worker_settings()
    if not options.MINIMAX_API_KEY.get_secret_value().strip():
        raise SystemExit("Set MINIMAX_API_KEY before starting the i2v worker.")
    if not which(options.I2V_FFMPEG):
        raise SystemExit("Install ffmpeg or set I2V_FFMPEG to its executable.")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@signals.worker_process_init.connect
def reset_database_pool(**_kwargs) -> None:
    sync_engine.dispose(close=False)


@celery_app.task(bind=True, name="i2v.generate_video", max_retries=None)
def enqueue_video_generation(
    self,
    work_id: int,
    image_url: str,
    prompt: str,
    engine_code: str,
    duration: int = 15,
    aspect_ratio: str = "16:9",
) -> None:
    # Accept the gateway's kwargs verbatim. Fetch the original image, prompt, and engine from
    # committed state so broker redelivery still works after a presigned URL expires.
    try:
        lock = work_lock(work_id)
        if not lock.acquire():
            raise self.retry(countdown=30 + uniform(0, 5))
        try:
            job = load_job(work_id)
            if job is None:
                return
            code = job.work.engine_code or engine_code
            if not job.work.upstream_task_id and not queue_expired(job) and circuit_is_open(code):
                raise self.retry(countdown=20 + uniform(0, 10))
            limit = max(1, job.engine.max_concurrency) if job.engine is not None else 1
            with generation_slot(code, limit, worker_settings().I2V_ACCOUNT_CONCURRENCY) as acquired:
                if not acquired:
                    raise self.retry(countdown=3 + uniform(0, 3))
                generate(job, duration, job.work.aspect_ratio or aspect_ratio)
        finally:
            lock.release()
    except (RedisError, SQLAlchemyError):
        logger.warning("Worker dependency unavailable for work_id=%s", work_id)
        raise self.retry(countdown=15 + uniform(0, 5)) from None


@celery_app.task(name="i2v.reconcile_stale")
def reconcile_stale() -> None:
    for work_id in stale_work_ids():
        enqueue_video_generation.delay(work_id=work_id, image_url="", prompt="", engine_code="")
