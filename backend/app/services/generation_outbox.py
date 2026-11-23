"""Claim and acknowledge generation messages using MySQL."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import GenerationOutbox, Work

LEASE = timedelta(seconds=45)
REDELIVERY_DELAY = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class Delivery:
    work_id: int
    token: str
    attempt: int


def _active_work():
    return (
        select(Work.id)
        .where(Work.id == GenerationOutbox.work_id, Work.task_status.in_(("queued", "running")))
        .exists()
    )


def claim_delivery() -> Delivery | None:
    now = utcnow()
    with SyncSessionLocal.begin() as session:
        event = session.scalar(
            select(GenerationOutbox)
            .where(
                GenerationOutbox.received_at.is_(None),
                GenerationOutbox.dispatch_after <= now,
                _active_work(),
                or_(GenerationOutbox.leased_until.is_(None), GenerationOutbox.leased_until <= now),
            )
            .order_by(GenerationOutbox.dispatch_after, GenerationOutbox.work_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return None
        event.attempts += 1
        event.lease_token = token = uuid4().hex
        event.leased_until = now + LEASE
        return Delivery(event.work_id, token, event.attempts)


def finish_delivery(delivery: Delivery, *, error: str | None = None) -> None:
    now = utcnow()
    retry_delay = timedelta(seconds=min(2 ** min(delivery.attempt, 8), 300))
    with SyncSessionLocal.begin() as session:
        statement = (
            update(GenerationOutbox)
            .where(
                GenerationOutbox.work_id == delivery.work_id, GenerationOutbox.lease_token == delivery.token
            )
            .values(
                lease_token=None,
                leased_until=None,
                last_error=error,
                dispatch_after=now + (retry_delay if error else REDELIVERY_DELAY),
            )
        )
        if not error:
            statement = statement.values(published_at=now)
        session.execute(statement)


def receive_delivery(work_id: int) -> bool:
    """Only one consumer claims a fresh dispatch for this Work."""
    with SyncSessionLocal.begin() as session:
        result = session.execute(
            update(GenerationOutbox)
            .where(GenerationOutbox.work_id == work_id, GenerationOutbox.received_at.is_(None))
            .values(received_at=utcnow())
        )
        return result.rowcount == 1


def delayed_delivery_count(age: timedelta) -> int:
    with SyncSessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(GenerationOutbox)
                .where(
                    GenerationOutbox.received_at.is_(None),
                    GenerationOutbox.created_at <= utcnow() - age,
                    _active_work(),
                )
            )
            or 0
        )
