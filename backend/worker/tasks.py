"""Celery entry points matching the existing FastAPI handoff."""

import logging
from random import uniform
from shutil import which

from celery import Celery, signals
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings, is_https_endpoint
from app.core.database import sync_engine
from worker.config import worker_settings
from worker.execution import acquire, maintain, reserve_recovery
from worker.generation_reconciler import log_recovery_backlog, review_due_submissions
from worker.media_workspace import cleanup_orphans
from worker.pipeline import generate
from worker.state import (
    TASK_SECONDS,
    circuit_is_open,
    generation_slot,
    load_job,
    stale_work_ids,
    work_lock,
)

QUEUE = "i2v_generate"
RECOVERY_QUEUE = "i2v_recovery"
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
    broker_connection_timeout=5,
    broker_transport_options={"visibility_timeout": 3600, "socket_connect_timeout": 5, "socket_timeout": 5},
    beat_schedule={
        "reconcile-video-works": {
            "task": "i2v.reconcile_stale",
            "schedule": 60.0,
            "options": {"queue": RECOVERY_QUEUE},
        },
    },
)

logger = logging.getLogger(__name__)


@signals.worker_init.connect
def validate_worker(**_kwargs) -> None:
    options = worker_settings()
    if not options.MINIMAX_API_KEY.get_secret_value().strip():
        raise SystemExit("Set MINIMAX_API_KEY before starting the i2v worker.")
    if get_settings().is_production and not is_https_endpoint(options.MINIMAX_BASE_URL):
        raise SystemExit("Set MINIMAX_BASE_URL to an HTTPS endpoint in production.")
    if not which(options.I2V_FFMPEG):
        raise SystemExit("Install ffmpeg or set I2V_FFMPEG to its executable.")
    if not which(options.I2V_FFPROBE):
        raise SystemExit("Install ffprobe or set I2V_FFPROBE to its executable.")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@signals.worker_process_init.connect
def reset_database_pool(**_kwargs) -> None:
    sync_engine.dispose(close=False)


@celery_app.task(bind=True, name="i2v.generate_video", max_retries=3)
def enqueue_video_generation(
    self,
    work_id: int,
    image_url: str = "",
    prompt: str = "",
    engine_code: str = "",
    duration: int = 15,
    aspect_ratio: str = "16:9",
    from_outbox: bool = False,
) -> None:
    # Accept the gateway's kwargs verbatim. Fetch the original image, prompt, and engine from
    # committed state so broker redelivery still works after a presigned URL expires.
    try:
        lock = work_lock(work_id)
        if not lock.acquire():
            logger.info("duplicate_delivery_redis_lock work_id=%s", work_id)
            return
        try:
            job = load_job(work_id)
            if job is None:
                return
            code = job.work.engine_code or engine_code
            if not job.work.upstream_task_id and circuit_is_open(code):
                raise self.retry(countdown=20 + uniform(0, 10))
            limit = max(1, job.engine.max_concurrency) if job.engine is not None else 1
        finally:
            try:
                lock.release()
            except RedisError:
                logger.warning("redis_lock_release_failed work_id=%s", work_id)
        with generation_slot(code, limit, worker_settings().I2V_ACCOUNT_CONCURRENCY) as slot:
            if not slot:
                raise self.retry(countdown=3 + uniform(0, 3))
            lease = acquire(work_id, from_outbox=from_outbox, max_seconds=TASK_SECONDS + 15)
            if lease is None:
                return
            with maintain(lease, refresh_slot=slot) as lost:
                generate(job, duration, job.work.aspect_ratio or aspect_ratio, lease=lease, lost=lost)
    except (RedisError, SQLAlchemyError):
        logger.warning("Worker dependency unavailable for work_id=%s", work_id)
        raise self.retry(countdown=15 + uniform(0, 5)) from None


@celery_app.task(name="i2v.reconcile_stale", queue=RECOVERY_QUEUE, expires=60)
def reconcile_stale() -> None:
    review_due_submissions()
    log_recovery_backlog()
    cleanup_orphans()
    for work_id in stale_work_ids():
        if reserve_recovery(work_id):
            try:
                enqueue_video_generation.apply_async(kwargs={"work_id": work_id}, retry=False)
                logger.info("execution_recovery_published work_id=%s", work_id)
            except (OperationalError, RedisError):
                logger.warning("execution_recovery_publish_failed work_id=%s", work_id)
