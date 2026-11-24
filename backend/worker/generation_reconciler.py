"""Review unresolved submissions and attach IDs verified by an operator."""

import argparse
import logging
import os
import re

import httpx
from sqlalchemy import func, select

from app.core.database import SyncSessionLocal
from app.models import AuditLog, GenerationAttempt, GenerationExecution, Work
from app.services.execution_lease import ACTIVE_WORK, ExecutionStopped, active_work, check_lease, database_now
from worker.attempts import REVIEW_REMINDER, UNKNOWN, latest_attempt, transition
from worker.config import worker_settings
from worker.minimax import MiniMax, valid_task_id

logger = logging.getLogger(__name__)


def log_recovery_backlog() -> None:
    with SyncSessionLocal() as session:
        now = database_now(session)
        pending = dict(
            session.execute(
                select(GenerationAttempt.status, func.count())
                .where(GenerationAttempt.status.in_(UNKNOWN))
                .group_by(GenerationAttempt.status)
            ).all()
        )
        expired = session.scalar(
            select(func.count())
            .select_from(GenerationExecution)
            .join(Work, Work.id == GenerationExecution.work_id)
            .where(Work.task_status.in_(ACTIVE_WORK), GenerationExecution.lease_expires_at <= now)
        )
    logger.log(
        logging.WARNING if pending or expired else logging.INFO,
        "generation_recovery_backlog unknown=%s manual_review=%s expired_leases=%s",
        pending.get("submission_unknown", 0),
        pending.get("manual_review", 0),
        expired,
    )


def review_due_submissions() -> None:
    with SyncSessionLocal() as session:
        due = session.execute(
            select(GenerationAttempt.work_id, GenerationAttempt.id)
            .where(
                GenerationAttempt.status.in_(UNKNOWN),
                GenerationAttempt.next_check_at <= database_now(session),
            )
            .order_by(GenerationAttempt.next_check_at, GenerationAttempt.id)
            .limit(100)
        ).all()

    for work_id, attempt_id in due:
        with SyncSessionLocal.begin() as session:
            try:
                work = active_work(session, work_id)
                check_lease(session, work, None, operator=True)
            except ExecutionStopped:
                continue
            attempt = latest_attempt(session, work.id)
            now = database_now(session)
            if not attempt or attempt.id != attempt_id or attempt.status not in UNKNOWN:
                continue
            if attempt.next_check_at is None or attempt.next_check_at > now:
                continue
            if attempt.status == "submission_unknown":
                transition(attempt, "manual_review", now=now, error=attempt.error_code)
            attempt.last_checked_at = now
            attempt.next_check_at = now + REVIEW_REMINDER
            logger.warning(
                "generation_manual_review work_id=%s attempt_id=%s request_id=%s provider_task_id=%s code=%s",
                work_id,
                attempt.id,
                attempt.request_id,
                attempt.provider_task_id,
                attempt.error_code,
            )


def shell_operator() -> str:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        length = wintypes.DWORD(257)
        name = ctypes.create_unicode_buffer(length.value)
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.GetUserNameW.argtypes = (wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        if not api.GetUserNameW(name, ctypes.byref(length)):
            raise RuntimeError("Cannot establish the Windows shell account")
        username = name.value
    else:
        import pwd

        username = pwd.getpwuid(os.geteuid()).pw_name
    if not username or len(username) > 58 or not username.isprintable():
        raise RuntimeError("Cannot represent the shell account in the audit record")
    return f"SHELL:{username}"


def link_verified_task(
    work_id: int, request_id: str, task_id: str, provider: MiniMax, *, reason: str
) -> None:
    if not re.fullmatch(r"(?:INC|CASE|SUPPORT|EXAM)-[0-9]{1,20}", reason):
        raise ValueError("Use a reconciliation evidence reference such as INC-123.")
    operator = shell_operator()
    if not valid_task_id(task_id):
        raise ValueError("Invalid provider task ID")
    status = provider.query(task_id, 15)
    if status.get("id") != task_id or status.get("status") not in {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }:
        raise ValueError("Provider did not verify the exact task ID and status")
    if status["status"] in {"failed", "cancelled"}:
        raise ValueError(f"Provider task already terminated ({status['status']}); cannot bind it")

    with SyncSessionLocal.begin() as session:
        work = active_work(session, work_id)
        execution = check_lease(session, work, None, operator=True)
        attempt = latest_attempt(session, work_id)
        if attempt is None or attempt.request_id != request_id or attempt.status not in UNKNOWN:
            raise ExecutionStopped("Request is not awaiting reconciliation")
        if attempt.provider_task_id and attempt.provider_task_id != task_id:
            raise ValueError("Cannot replace a previously verified task ID")
        if attempt.error_code == "MEDIA_FAILED" and status["status"] != "succeeded":
            raise ValueError("Previously successful upstream task could not be confirmed")
        target = "uploading" if attempt.error_code == "MEDIA_FAILED" else "accepted"
        now = database_now(session)
        before = {"status": attempt.status, "provider_task_id": attempt.provider_task_id}
        transition(attempt, target, now=now, task_id=task_id)
        session.add(
            AuditLog(
                admin_id=0,
                admin_username=operator,
                action="link_provider_task",
                target_type="work",
                target_id=work.task_no,
                before_val=before,
                after_val={
                    "status": target,
                    "work_id": work.id,
                    "attempt_id": attempt.id,
                    "request_id": request_id,
                    "provider_task_id": task_id,
                    "correlation_result": "provider_id_verified_operator_attested",
                    "provider_status": status["status"],
                },
                remark=reason,
                created_at=now,
            )
        )
        work.task_status = "running"
        work.upstream_task_id = task_id
        if execution:
            execution.recover_after = now
    logger.warning(
        "operator_verified_task work_id=%s request_id=%s provider_task_id=%s", work_id, request_id, task_id
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_id", type=int)
    parser.add_argument("request_id")
    parser.add_argument("task_id")
    parser.add_argument("--confirm-provider-correlation", required=True, action="store_true")
    parser.add_argument("--reason", required=True, help="Evidence reference, e.g. INC-123")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    options = worker_settings()
    with httpx.Client() as client:
        provider = MiniMax(client, options.MINIMAX_API_KEY.get_secret_value(), options.MINIMAX_BASE_URL)
        link_verified_task(args.work_id, args.request_id, args.task_id, provider, reason=args.reason)


if __name__ == "__main__":
    main()
