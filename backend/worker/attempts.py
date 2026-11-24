"""Persist and transition provider attempts under the Work row lock."""

import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import GenerationAttempt, Work
from app.services.execution_lease import ExecutionLease, ExecutionStopped, active_work, check_lease
from worker.minimax import valid_task_id

logger = logging.getLogger(__name__)

UNKNOWN = ("submission_unknown", "manual_review")
TRANSITIONS = {
    "prepared": {"submitting", "failed"},
    "submitting": {"accepted", "submission_unknown", "failed"},
    "submission_unknown": {"manual_review", "accepted"},
    "manual_review": {"accepted", "uploading"},
    "accepted": {"running", "uploading", "failed", "cancelled", "manual_review"},
    "running": {"uploading", "failed", "cancelled", "manual_review"},
    "uploading": {"succeeded", "manual_review"},
}
MANUAL_REVIEW_DELAY = timedelta(minutes=15)
REVIEW_REMINDER = timedelta(hours=1)


def latest_attempt(session, work_id: int) -> GenerationAttempt | None:
    return session.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.work_id == work_id)
        .order_by(GenerationAttempt.attempt_no.desc())
        .limit(1)
        .with_for_update()
    )


def _new_attempt(session, work: Work, status: str, attempt_no: int, *, task_id: str | None = None):
    now = utcnow()
    attempt = GenerationAttempt(
        work_id=work.id,
        attempt_no=attempt_no,
        request_id=uuid4().hex,
        provider="minimax",
        status=status,
        provider_task_id=task_id,
        created_at=now,
        updated_at=now,
        next_check_at=now if status == "submission_unknown" else None,
    )
    session.add(attempt)
    session.flush()
    return attempt


def prepare_attempt(
    work_id: int, *, allow_new: bool = True, lease: ExecutionLease | None = None
) -> GenerationAttempt:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, work_id)
        check_lease(session, work, lease)
        prior = latest_attempt(session, work_id)
        if prior is None:
            if valid_task_id(work.upstream_task_id) and not work.upstream_task_id.startswith("mock-"):
                return _new_attempt(
                    session, work, "accepted", work.retry_count + 1, task_id=work.upstream_task_id
                )
            if not allow_new or work.upstream_task_id:
                work.task_status = "running"
                return _new_attempt(session, work, "submission_unknown", work.retry_count + 1)
            return _new_attempt(session, work, "prepared", work.retry_count + 1)
        if prior.status == "failed" and prior.retry_create and work.retry_count < 1:
            work.retry_count += 1
            work.upstream_task_id = None
            return _new_attempt(session, work, "prepared", prior.attempt_no + 1)
        return prior


def transition(
    attempt: GenerationAttempt,
    target: str,
    *,
    error: str | None = None,
    task_id: str | None = None,
    retry_create: bool = False,
) -> None:
    if target not in TRANSITIONS.get(attempt.status, ()):
        raise ExecutionStopped(f"Invalid attempt transition {attempt.status} -> {target}")
    now = utcnow()
    if target == "submitting":
        attempt.request_started_at = now
    if attempt.status == "submitting" and target in {"accepted", "failed"}:
        attempt.response_received_at = now
    if target in {"accepted", "running", "uploading", "failed", "cancelled"}:
        attempt.last_checked_at = now
    if task_id:
        attempt.provider_task_id = task_id
    attempt.status = target
    attempt.error_code = error
    attempt.retry_create = int(retry_create)
    attempt.updated_at = now
    attempt.next_check_at = now + MANUAL_REVIEW_DELAY if target in UNKNOWN else None


def note_query(attempt: GenerationAttempt, lease: ExecutionLease) -> None:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, attempt.work_id)
        check_lease(session, work, lease)
        current = latest_attempt(session, work.id)
        if current is None or current.id != attempt.id or current.status != attempt.status:
            raise ExecutionStopped(work.id)
        current.last_checked_at = utcnow()


def change_attempt(
    attempt: GenerationAttempt, target: str, *, lease: ExecutionLease | None = None, **options
) -> GenerationAttempt:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, attempt.work_id)
        check_lease(session, work, lease)
        current = latest_attempt(session, work.id)
        if current is None or current.id != attempt.id or current.status != attempt.status:
            raise ExecutionStopped(work.id)
        transition(current, target, **options)
        if target in {"submitting", "submission_unknown", "accepted"}:
            work.task_status = "running"
        if target == "accepted":
            work.upstream_task_id = current.provider_task_id
    logger.info(
        "generation_attempt work_id=%s attempt_id=%s request_id=%s provider_task_id=%s "
        "lease_owner=%s lease_generation=%s status=%s code=%s",
        current.work_id,
        current.id,
        current.request_id,
        current.provider_task_id,
        lease.owner if lease else None,
        lease.generation if lease else None,
        current.status,
        current.error_code,
    )
    return current
