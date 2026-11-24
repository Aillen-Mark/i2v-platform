"""Execution ownership shared by the Worker and the settlement callbacks."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text

from app.models import GenerationExecution, Work

ACTIVE_WORK = ("queued", "running")


class ExecutionStopped(RuntimeError):
    """A terminal Work or a competing execution prevents this operation."""


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    work_id: int
    owner: str
    generation: int
    allow_new: bool
    hard_deadline: datetime
    started_at: datetime


def database_now(session):
    return session.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one()


def active_work(session, work_id: int) -> Work:
    work = session.scalar(select(Work).where(Work.id == work_id).with_for_update())
    if work is None or work.task_status not in ACTIVE_WORK:
        raise ExecutionStopped(work_id)
    return work


def execution_row(session, work_id: int):
    return session.scalar(
        select(GenerationExecution).where(GenerationExecution.work_id == work_id).with_for_update()
    )


def check_lease(session, work: Work, lease: ExecutionLease | None, *, operator: bool = False):
    execution = execution_row(session, work.id)
    if lease is None:
        if execution is None or (operator and not is_live(execution, database_now(session))):
            return execution
        raise ExecutionStopped(work.id)
    if (
        execution is None
        or lease.work_id != work.id
        or execution.lease_owner != lease.owner
        or execution.lease_generation != lease.generation
        or not is_live(execution, database_now(session))
    ):
        raise ExecutionStopped(work.id)
    return execution


def is_live(execution: GenerationExecution, now) -> bool:
    return execution.lease_expires_at is not None and execution.lease_expires_at > now
