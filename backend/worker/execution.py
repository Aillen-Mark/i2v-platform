"""Claim, renew and release a database execution lease."""

import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SyncSessionLocal
from app.models import GenerationExecution, GenerationOutbox
from app.services.execution_lease import (
    ExecutionLease,
    ExecutionStopped,
    active_work,
    check_lease,
    database_now,
    execution_row,
    is_live,
)
from worker.attempts import UNKNOWN, latest_attempt
from worker.config import worker_settings

logger = logging.getLogger(__name__)
RECOVERY_DELAY = timedelta(seconds=30)
RECOVERY_PUBLISH_INTERVAL = timedelta(minutes=2)


def new_execution(work, delivery, now) -> GenerationExecution:
    return GenerationExecution(
        work_id=work.id,
        lease_generation=0,
        started_at=now,
        recover_after=now,
        legacy_uncertain=int(
            not work.upstream_task_id and (delivery is None or delivery.received_at is not None)
        ),
    )


def reserve_recovery(work_id: int) -> bool:
    with SyncSessionLocal.begin() as session:
        try:
            work = active_work(session, work_id)
        except ExecutionStopped:
            return False
        now = database_now(session)
        row = execution_row(session, work_id)
        if row and (is_live(row, now) or row.recover_after > now):
            return False
        delivery = session.get(GenerationOutbox, work_id)
        if delivery and delivery.received_at is None:
            return False
        attempt = latest_attempt(session, work_id)
        if attempt and attempt.status in UNKNOWN:
            return False
        if row is None:
            row = new_execution(work, delivery, now)
            session.add(row)
        row.recover_after = now + RECOVERY_PUBLISH_INTERVAL
    return True


def acquire(work_id: int, *, from_outbox: bool, max_seconds: int) -> ExecutionLease | None:
    with SyncSessionLocal.begin() as session:
        try:
            work = active_work(session, work_id)
        except ExecutionStopped:
            return None
        row = execution_row(session, work_id)
        now = database_now(session)
        if row is not None and is_live(row, now):
            logger.info(
                "duplicate_delivery work_id=%s lease_owner=%s lease_generation=%s",
                work_id,
                row.lease_owner,
                row.lease_generation,
            )
            return None
        delivery = session.get(GenerationOutbox, work_id, with_for_update=True)
        if delivery and delivery.received_at is None and not from_outbox:
            return None
        attempt = latest_attempt(session, work_id)
        if attempt is not None and attempt.status in UNKNOWN:
            return None
        if row is None:
            row = new_execution(work, delivery, now)
            session.add(row)
        deadline = now + timedelta(seconds=max_seconds)
        row.lease_owner = uuid4().hex
        row.lease_generation += 1
        row.lease_expires_at = min(deadline, now + timedelta(seconds=worker_settings().I2V_LEASE_SECONDS))
        row.last_heartbeat_at = now
        row.recover_after = row.lease_expires_at
        if delivery and delivery.received_at is None:
            delivery.received_at = now
        lease = ExecutionLease(
            work_id,
            row.lease_owner,
            row.lease_generation,
            not row.legacy_uncertain,
            deadline,
            row.started_at,
        )
    logger.info(
        "execution_acquired work_id=%s lease_owner=%s lease_generation=%s takeover=%s",
        work_id,
        lease.owner,
        lease.generation,
        lease.generation > 1,
    )
    return lease


def renew(lease: ExecutionLease) -> None:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, lease.work_id)
        row = check_lease(session, work, lease)
        now = database_now(session)
        if now >= lease.hard_deadline:
            raise ExecutionStopped(lease.work_id)
        row.last_heartbeat_at = now
        row.lease_expires_at = min(
            lease.hard_deadline, now + timedelta(seconds=worker_settings().I2V_LEASE_SECONDS)
        )
        row.recover_after = row.lease_expires_at


def release(lease: ExecutionLease) -> None:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, lease.work_id)
        row = check_lease(session, work, lease)
        row.lease_owner = None
        row.lease_expires_at = None
        row.recover_after = database_now(session) + RECOVERY_DELAY


@contextmanager
def maintain(lease: ExecutionLease, refresh_slot=None):
    stopped, lost = Event(), Event()

    def heartbeat():
        while not stopped.wait(worker_settings().I2V_HEARTBEAT_SECONDS):
            try:
                renew(lease)
                if refresh_slot is not None:
                    refresh_slot()
            except (ExecutionStopped, RedisError, SQLAlchemyError):
                lost.set()
                logger.warning(
                    "execution_heartbeat_lost work_id=%s lease_owner=%s lease_generation=%s",
                    lease.work_id,
                    lease.owner,
                    lease.generation,
                )
                return

    thread = Thread(target=heartbeat, name=f"generation-heartbeat-{lease.work_id}", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stopped.set()
        thread.join(timeout=1)
        if not lost.is_set():
            try:
                release(lease)
            except ExecutionStopped:
                # A terminal Work or an expired lease requires no release.
                pass
            except SQLAlchemyError:
                logger.warning("execution_release_failed work_id=%s", lease.work_id)
