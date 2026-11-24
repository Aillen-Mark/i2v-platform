"""Paged MySQL views of successful media and outstanding references."""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from app.core.database import SyncSessionLocal
from app.models import GenerationExecution, Work
from app.services.execution_lease import ACTIVE_WORK, database_now

GENERATED = re.compile(r"videos/generated/([1-9][0-9]*)/[^/]+-[1-9][0-9]*\.mp4")
DIGEST = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True, slots=True)
class Reference:
    work_id: int
    task_no: str
    key: str | None
    size: int | None
    digest: str | None
    finished_at: datetime | None
    duration: int | None
    checkpoint_conflict: bool

    @property
    def kind(self):
        return "mock" if self.key and self.key.startswith("videos/mock/") else "work_video"


def successful_page(after: int, limit: int, sessions=SyncSessionLocal) -> list[Reference]:
    with sessions() as session:
        rows = session.execute(
            select(
                Work.id,
                Work.task_no,
                Work.video_key,
                Work.file_size,
                Work.finished_at,
                Work.duration_sec,
                GenerationExecution.artifact_key,
                GenerationExecution.artifact_size,
                GenerationExecution.artifact_sha256,
            )
            .outerjoin(GenerationExecution, GenerationExecution.work_id == Work.id)
            .where(Work.task_status == "succeeded", Work.id > after)
            .order_by(Work.id)
            .limit(limit)
        ).all()
    result = []
    for work_id, task_no, key, size, finished, duration, check_key, check_size, check_hash in rows:
        same = key is not None and key == check_key
        digest = check_hash if same and DIGEST.fullmatch(check_hash or "") else None
        conflict = bool(
            check_key and (not same or (size is not None and check_size is not None and check_size != size))
        )
        result.append(
            Reference(
                work_id,
                task_no,
                key,
                size if size is not None else check_size if same else None,
                digest,
                finished,
                duration,
                conflict,
            )
        )
    return result


def successful_refs(batch_size: int, sessions=SyncSessionLocal):
    after = 0
    while page := successful_page(after, batch_size, sessions):
        yield from page
        after = page[-1].work_id


def active_count(sessions=SyncSessionLocal) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(Work).where(Work.task_status.in_(ACTIVE_WORK)))


def work_links(keys: set[str], sessions=SyncSessionLocal) -> dict[str, set[int]]:
    if not keys:
        return {}
    result = defaultdict(set)
    with sessions() as session:
        for key, work_id in session.execute(select(Work.video_key, Work.id).where(Work.video_key.in_(keys))):
            if key in keys:
                result[key].add(work_id)
        for key, work_id in session.execute(
            select(GenerationExecution.artifact_key, GenerationExecution.work_id).where(
                GenerationExecution.artifact_key.in_(keys)
            )
        ):
            if key in keys:
                result[key].add(work_id)
    return result


def object_claims(keys: set[str], sessions=SyncSessionLocal):
    """Get known checkpoint links plus Work owners inferred from generated keys."""
    if not keys:
        return {}, {}
    owners = {int(match[1]) for key in keys if (match := GENERATED.fullmatch(key)) and len(match[1]) <= 19}
    checkpoints = defaultdict(list)
    with sessions() as session:
        now = database_now(session)
        for key, work_id, owner, generation, expires, recover_after, status in session.execute(
            select(
                GenerationExecution.artifact_key,
                GenerationExecution.work_id,
                GenerationExecution.lease_owner,
                GenerationExecution.lease_generation,
                GenerationExecution.lease_expires_at,
                GenerationExecution.recover_after,
                Work.task_status,
            )
            .outerjoin(Work, Work.id == GenerationExecution.work_id)
            .where(GenerationExecution.artifact_key.in_(keys))
        ):
            if key in keys:
                checkpoints[key].append(
                    (
                        work_id,
                        bool(owner and expires and expires > now) or status in ACTIVE_WORK,
                        generation,
                        recover_after,
                    )
                )
        inferred = dict(session.execute(select(Work.id, Work.task_status).where(Work.id.in_(owners))).all())
    return checkpoints, inferred
