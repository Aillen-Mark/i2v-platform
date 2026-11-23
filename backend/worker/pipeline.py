"""Run and reconcile a generation using the business handoff callbacks."""

import logging
from datetime import timedelta
from pathlib import Path
from random import uniform
from tempfile import TemporaryDirectory
from time import sleep, time

import httpx
from billiard.exceptions import SoftTimeLimitExceeded
from botocore.exceptions import BotoCoreError, ClientError
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.storage import upload_bytes
from app.core.timeutil import utcnow
from app.services.handoff_sync import (
    QUEUE_KEY,
    mark_work_failed_and_refund,
    mark_work_succeeded,
    sync_redis,
)
from worker.config import worker_settings
from worker.media import prepare_first_frame, prepare_video, remaining_seconds
from worker.minimax import MESSAGES, GenerationError, MiniMax
from worker.state import (
    QUEUE_SECONDS,
    TASK_SECONDS,
    Job,
    generation_start,
    record_outcome,
    save_progress,
    submission_key,
)

logger = logging.getLogger(__name__)


def queue_expired(job: Job) -> bool:
    submitted = job.work.submitted_at
    return not job.work.upstream_task_id and (
        submitted is not None and submitted < utcnow() - timedelta(seconds=QUEUE_SECONDS)
    )


def create_upstream(provider: MiniMax, job: Job, duration: int, ratio: str, deadline: float) -> bool:
    work, engine = job.work, job.engine
    if engine is None or engine.provider != "minimax":
        raise GenerationError()
    marker = submission_key(work.id, work.retry_count)
    prior_submission = sync_redis.get(marker)
    if prior_submission:
        if prior_submission == "pending":
            raise GenerationError()
        return save_progress(job, prior_submission, work.retry_count)

    image = prepare_first_frame(work.image_key, ratio, deadline)
    timeout = min(20, remaining_seconds(deadline))
    if not sync_redis.set(marker, "pending", nx=True, ex=86400):
        raise GenerationError()
    try:
        task_id = provider.create(engine.model_name, engine.resolution, duration, image, work.prompt, timeout)
    except GenerationError as error:
        # Keep the marker when submission may have succeeded but returned no usable task ID.
        if error.retry == "resubmit":
            sync_redis.delete(marker)
        raise
    sync_redis.set(marker, task_id, ex=86400)
    return save_progress(job, task_id, work.retry_count)


def wait_for_result(provider: MiniMax, task_id: str, deadline: float) -> str:
    while True:
        task = provider.query(task_id, min(15, remaining_seconds(deadline)))
        url = provider.result_url(task)
        if url:
            return url
        sleep(min(worker_settings().I2V_POLL_INTERVAL_SECONDS, remaining_seconds(deadline)))


def settle(job: Job, success: bool, seconds: int, error: GenerationError | None = None) -> None:
    engine_code = job.work.engine_code
    if engine_code:
        try:
            record_outcome(
                engine_code,
                success,
                seconds,
                outage=bool(error and error.code in {"UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"}),
            )
        except RedisError:
            logger.warning("Engine health update failed for work_id=%s", job.work.id)


def refund(job: Job, error: GenerationError) -> None:
    mark_work_failed_and_refund(job.work.id, error.code, MESSAGES[error.code], job.work.retry_count)
    settle(job, False, 0, error)


def upload_and_finish(
    client: httpx.Client, job: Job, url: str, duration: int, started: float, deadline: float
) -> None:
    work = job.work
    video_key = f"videos/{job.account}/{work.task_no}.mp4"
    with TemporaryDirectory(prefix="i2v-") as directory:
        data = prepare_video(client, url, Path(directory), duration, deadline)
        remaining_seconds(deadline)
        try:
            upload_bytes(get_settings().MINIO_BUCKET_VIDEOS, video_key, data, "video/mp4")
        except (BotoCoreError, ClientError):
            raise GenerationError("MEDIA_FAILED", retry="resume") from None
    seconds = max(1, round(time() - started))
    remaining_seconds(deadline)
    if work.upstream_task_id is None:
        raise GenerationError()
    mark_work_succeeded(work.id, video_key, len(data), duration, seconds, work.upstream_task_id)
    settle(job, True, seconds)


def timeout_or_recover(
    provider: MiniMax, client: httpx.Client, job: Job, duration: int, start: float, *, recover: bool
) -> None:
    task_id = job.work.upstream_task_id
    if task_id:
        try:
            task = provider.query(task_id, 8)
            if task.get("status") == "queued":
                provider.cancel_queued(task_id, 8)
            elif recover and task.get("status") == "succeeded":
                url = provider.result_url(task)
                if url:
                    upload_and_finish(client, job, url, duration, start, time() + 60)
                    return
        except GenerationError as error:
            logger.info("Upstream timeout cleanup work_id=%s code=%s", job.work.id, error.code)
    refund(job, GenerationError("UPSTREAM_TIMEOUT"))


def generate(job: Job, duration: int, ratio: str) -> None:
    work = job.work
    if queue_expired(job):
        refund(job, GenerationError("UPSTREAM_TIMEOUT"))
        return
    start = generation_start(work.id)
    deadline = start + TASK_SECONDS
    duration = work.duration_sec if work.duration_sec is not None else duration
    sync_redis.zrem(QUEUE_KEY, str(work.id))
    options = worker_settings()
    with httpx.Client() as client:
        provider = MiniMax(client, options.MINIMAX_API_KEY.get_secret_value(), options.MINIMAX_BASE_URL)
        if time() >= deadline:
            timeout_or_recover(provider, client, job, duration, start, recover=True)
            return
        while True:
            try:
                if not work.upstream_task_id and not create_upstream(
                    provider, job, duration, ratio, deadline
                ):
                    return
                task_id = work.upstream_task_id
                if task_id is None:
                    raise GenerationError()
                url = wait_for_result(provider, task_id, deadline)
                upload_and_finish(client, job, url, duration, start, deadline)
                return
            except SoftTimeLimitExceeded:
                timeout_or_recover(provider, client, job, duration, start, recover=False)
                return
            except GenerationError as error:
                if error.code == "UPSTREAM_TIMEOUT" or time() >= deadline:
                    timeout_or_recover(provider, client, job, duration, start, recover=False)
                    return
                if error.retry is None or work.retry_count >= 1:
                    refund(job, error)
                    return
                task_id = None if error.retry == "resubmit" else work.upstream_task_id
                if not save_progress(job, task_id, work.retry_count + 1):
                    return
                sleep(min(2 + uniform(0, 1), max(deadline - time(), 0)))
