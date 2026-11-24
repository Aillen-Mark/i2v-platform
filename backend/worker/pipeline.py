"""Generate a video and settle its Work from durable provider state."""

import logging
from datetime import timedelta
from pathlib import Path
from random import uniform
from tempfile import TemporaryDirectory
from time import sleep, time

import httpx
from billiard.exceptions import SoftTimeLimitExceeded
from redis.exceptions import RedisError

from app.core.timeutil import utcnow
from app.services.handoff_sync import (
    _remove_from_wait_queue,
    mark_work_failed_and_refund,
    mark_work_succeeded,
)
from app.services.execution_lease import ExecutionLease, ExecutionStopped
from worker import artifacts
from worker.attempts import UNKNOWN, change_attempt, note_query, prepare_attempt
from worker.config import worker_settings
from worker.media import prepare_first_frame, prepare_video, remaining_seconds
from worker.minimax import MESSAGES, GenerationError, MiniMax
from worker.state import TASK_SECONDS, Job, record_outcome

logger = logging.getLogger(__name__)


def require_heartbeat(lease, lost) -> None:
    if lost.is_set():
        raise ExecutionStopped(lease.work_id)


def create_upstream(provider, job, attempt, duration: int, ratio: str, deadline: float, lease, lost):
    work, engine = job.work, job.engine
    try:
        if engine is None or engine.provider != "minimax":
            raise GenerationError()
        image = prepare_first_frame(work.image_key, ratio, deadline)
        timeout = min(20, remaining_seconds(deadline))
    except GenerationError as error:
        return change_attempt(
            attempt, "failed", lease=lease, error=error.code, retry_create=error.retry == "resume"
        )
    require_heartbeat(lease, lost)
    attempt = change_attempt(attempt, "submitting", lease=lease)
    require_heartbeat(lease, lost)
    try:
        task_id = provider.create(engine.model_name, engine.resolution, duration, image, work.prompt, timeout)
    except GenerationError as error:
        code = "UPSTREAM_TIMEOUT" if error.timed_out else error.code
        if error.uncertain:
            return change_attempt(attempt, "submission_unknown", lease=lease, error=code)
        return change_attempt(
            attempt, "failed", lease=lease, error=code, retry_create=error.retry == "resubmit"
        )
    return change_attempt(attempt, "accepted", lease=lease, task_id=task_id)


def wait_for_result(provider, attempt, deadline: float, lease, lost):
    while True:
        require_heartbeat(lease, lost)
        try:
            task = provider.query(attempt.provider_task_id, min(15, remaining_seconds(deadline)))
        except GenerationError as error:
            if error.retry != "resume":
                raise
            logger.warning(
                "provider_query_retry work_id=%s attempt_id=%s request_id=%s provider_task_id=%s timeout=%s",
                attempt.work_id,
                attempt.id,
                attempt.request_id,
                attempt.provider_task_id,
                error.timed_out,
            )
            sleep(min(10, remaining_seconds(deadline)))
            continue
        note_query(attempt, lease)
        status = task.get("status")
        if status == "succeeded":
            if attempt.status != "uploading":
                attempt = change_attempt(attempt, "uploading", lease=lease)
            url = provider.result_url(task)
            return attempt, url
        if attempt.status == "uploading":
            raise GenerationError("MEDIA_FAILED", retry="resume")
        if status == "failed":
            try:
                provider.result_url(task)
            except GenerationError as error:
                attempt = change_attempt(
                    attempt, "failed", lease=lease, error=error.code, retry_create=error.retry == "resubmit"
                )
            return attempt, None
        if status == "cancelled":
            return change_attempt(attempt, "cancelled", lease=lease, error="UPSTREAM_ERROR"), None
        if status == "running" and attempt.status == "accepted":
            attempt = change_attempt(attempt, "running", lease=lease)
        sleep(min(worker_settings().I2V_POLL_INTERVAL_SECONDS, remaining_seconds(deadline)))


def settle(job: Job, success: bool, seconds: int, error_code: str | None = None) -> None:
    if job.work.engine_code:
        try:
            record_outcome(
                job.work.engine_code,
                success,
                seconds,
                outage=error_code in {"UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"},
            )
        except RedisError:
            logger.warning("Engine health update failed for work_id=%s", job.work.id)


def finish(job, attempt, artifact, duration, deadline, lease, lost) -> None:
    require_heartbeat(lease, lost)
    remaining_seconds(deadline)
    seconds = max(1, round((utcnow() - lease.started_at).total_seconds()))
    mark_work_succeeded(
        job.work.id,
        artifact.key,
        artifact.size,
        duration,
        seconds,
        attempt.provider_task_id,
        lease=lease,
    )
    settle(job, True, seconds)


def upload_and_finish(client, job, attempt, url, duration, deadline, lease, lost) -> None:
    with TemporaryDirectory(prefix="i2v-") as directory:
        data = prepare_video(client, url, Path(directory), duration, deadline)
        remaining_seconds(deadline)
        require_heartbeat(lease, lost)
        key = f"videos/generated/{job.work.id}/{job.work.task_no}-{lease.generation}.mp4"
        artifact = artifacts.upload(lease, key, data, lost)
    finish(job, attempt, artifact, duration, deadline, lease, lost)


def generate(job: Job, duration: int, ratio: str, *, lease: ExecutionLease, lost) -> None:
    deadline = time() + TASK_SECONDS
    work = job.work
    duration = work.duration_sec if work.duration_sec is not None else duration
    _remove_from_wait_queue(work.id)
    options = worker_settings()
    try:
        with httpx.Client() as client:
            provider = MiniMax(client, options.MINIMAX_API_KEY.get_secret_value(), options.MINIMAX_BASE_URL)
            while True:
                require_heartbeat(lease, lost)
                attempt = prepare_attempt(work.id, allow_new=lease.allow_new, lease=lease)
                if attempt.status == "submitting":
                    change_attempt(attempt, "submission_unknown", lease=lease, error="SUBMISSION_INTERRUPTED")
                    return
                if attempt.status in UNKNOWN:
                    return
                if attempt.status == "prepared":
                    attempt = create_upstream(provider, job, attempt, duration, ratio, deadline, lease, lost)
                if attempt.status == "uploading":
                    artifact = artifacts.recover(lease)
                    if artifact is not None:
                        finish(job, attempt, artifact, duration, deadline, lease, lost)
                        return
                if attempt.status in ("accepted", "running", "uploading") and utcnow() - (
                    attempt.updated_at
                ) >= timedelta(hours=options.I2V_RECONCILE_MAX_HOURS):
                    code = "MEDIA_FAILED" if attempt.status == "uploading" else "RECONCILIATION_OVERDUE"
                    change_attempt(attempt, "manual_review", lease=lease, error=code)
                    return
                if attempt.status in ("accepted", "running", "uploading"):
                    attempt, url = wait_for_result(provider, attempt, deadline, lease, lost)
                    if url:
                        try:
                            upload_and_finish(client, job, attempt, url, duration, deadline, lease, lost)
                        except GenerationError as error:
                            if error.retry is None and error.code == "MEDIA_FAILED":
                                change_attempt(attempt, "manual_review", lease=lease, error=error.code)
                                return
                            raise
                        return
                if attempt.status == "failed" and attempt.retry_create and attempt.attempt_no < 2:
                    sleep(min(2 + uniform(0, 1), remaining_seconds(deadline)))
                    continue
                if attempt.status in ("failed", "cancelled"):
                    error_code = attempt.error_code or "UPSTREAM_ERROR"
                    require_heartbeat(lease, lost)
                    mark_work_failed_and_refund(
                        work.id, error_code, MESSAGES[error_code], attempt.attempt_no - 1, lease=lease
                    )
                    settle(job, False, 0, error_code)
                return
    except ExecutionStopped:
        logger.info("generation_execution_stopped work_id=%s", work.id)
    except (GenerationError, SoftTimeLimitExceeded) as error:
        # The provider controls its own terminal state. A local deadline cannot settle the Work.
        logger.warning(
            "generation_incomplete work_id=%s lease_owner=%s lease_generation=%s code=%s",
            work.id,
            lease.owner,
            lease.generation,
            getattr(error, "code", "WORKER_TIME_LIMIT"),
        )
