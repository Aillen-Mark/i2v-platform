"""Worker-side SQL and Redis state."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from time import time
from typing import TypedDict
from uuid import uuid4

from sqlalchemy import or_, select, update

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import Candidate, Engine, Work
from app.services.handoff_sync import sync_redis

TASK_SECONDS = 600
LEASE_SECONDS = TASK_SECONDS + 90
QUEUE_SECONDS = 7200
ACTIVE_STATUSES = ("queued", "running")
GLOBAL_SLOTS = "i2v:worker:slots"

ACQUIRE_SLOTS = """
local now = tonumber(ARGV[1])
for i = 1, 2 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now)
    if redis.call('ZCARD', KEYS[i]) >= tonumber(ARGV[i + 3]) then
        return 0
    end
end
for i = 1, 2 do
    redis.call('ZADD', KEYS[i], ARGV[2], ARGV[3])
end
return 1
"""

UPDATE_HEALTH = """
local success = ARGV[1] == '1'
local outage = ARGV[2] == '1'
if success then
    redis.call('DEL', KEYS[4], KEYS[5])
    redis.call('LPUSH', KEYS[3], ARGV[3])
    redis.call('LTRIM', KEYS[3], 0, 19)
    local samples = redis.call('LRANGE', KEYS[3], 0, -1)
    local total = 0
    for _, value in ipairs(samples) do total = total + tonumber(value) end
    redis.call('SET', KEYS[6], math.floor(total / #samples + 0.5))
    redis.call('LPUSH', KEYS[7], ARGV[3])
    redis.call('LTRIM', KEYS[7], 0, 19)
    local engine_samples = redis.call('LRANGE', KEYS[7], 0, -1)
    local engine_total = 0
    for _, value in ipairs(engine_samples) do engine_total = engine_total + tonumber(value) end
    redis.call('HSET', KEYS[1], 'avg_cost_sec', math.floor(engine_total / #engine_samples + 0.5))
end
if outage then
    local count = redis.call('INCR', KEYS[4])
    redis.call('EXPIRE', KEYS[4], 120)
    if count >= 5 then redis.call('SET', KEYS[5], 1, 'EX', 60) end
end
redis.call('LPUSH', KEYS[2], ARGV[1])
redis.call('LTRIM', KEYS[2], 0, 19)
local successes = 0
local outcomes = redis.call('LRANGE', KEYS[2], 0, -1)
for _, value in ipairs(outcomes) do
    if value == '1' then successes = successes + 1 end
end
redis.call('HSET', KEYS[1],
    'recent_success', successes, 'recent_failed', #outcomes - successes,
    'circuit_open', redis.call('EXISTS', KEYS[5]))
"""


@dataclass
class Job:
    work: Work
    account: str
    engine: Engine | None


def load_job(work_id: int) -> Job | None:
    with SyncSessionLocal() as session:
        row = session.execute(
            select(Work, Candidate.account, Engine)
            .join(Candidate, Candidate.id == Work.candidate_id)
            .outerjoin(Engine, Engine.code == Work.engine_code)
            .where(Work.id == work_id, Work.task_status.in_(ACTIVE_STATUSES))
        ).one_or_none()
    return Job(*row) if row is not None else None


class WorkProgress(TypedDict, total=False):
    upstream_task_id: str | None
    retry_count: int
    task_status: str


def save_progress(job: Job, upstream_task_id: str | None, retry_count: int) -> bool:
    work = job.work
    updates: WorkProgress = {"upstream_task_id": upstream_task_id, "retry_count": retry_count}
    if upstream_task_id:
        updates["task_status"] = "running"
    with SyncSessionLocal.begin() as session:
        result = session.execute(
            update(Work).where(Work.id == work.id, Work.task_status.in_(ACTIVE_STATUSES)).values(**updates)
        )
        if result.rowcount != 1:
            return False
    for key, value in updates.items():
        setattr(work, key, value)
    return True


def stale_work_ids() -> list[int]:
    now = utcnow()
    with SyncSessionLocal() as session:
        return list(
            session.scalars(
                select(Work.id)
                .where(
                    or_(
                        (Work.task_status == "queued")
                        & (Work.submitted_at < now - timedelta(seconds=QUEUE_SECONDS)),
                        (Work.task_status == "running")
                        & (Work.updated_at < now - timedelta(seconds=LEASE_SECONDS)),
                    )
                )
                .order_by(Work.id)
                .limit(100)
            )
        )


def work_lock(work_id: int):
    return sync_redis.lock(f"i2v:worker:work:{work_id}", timeout=LEASE_SECONDS, blocking=False)


def generation_start(work_id: int) -> float:
    key = f"i2v:worker:started:{work_id}"
    now = time()
    old_start = sync_redis.set(key, str(now), nx=True, ex=86400)
    return now if old_start else float(sync_redis.get(key) or now)


def submission_key(work_id: int, attempt: int) -> str:
    return f"i2v:worker:submission:{work_id}:{attempt}"


@contextmanager
def generation_slot(engine_code: str, engine_limit: int, account_limit: int):
    engine_slots = f"{GLOBAL_SLOTS}:{engine_code}"
    token = uuid4().hex
    now = time()
    acquired = sync_redis.eval(
        ACQUIRE_SLOTS,
        2,
        GLOBAL_SLOTS,
        engine_slots,
        now,
        now + LEASE_SECONDS,
        token,
        account_limit,
        engine_limit,
    )
    try:
        yield bool(acquired)
    finally:
        if acquired:
            with sync_redis.pipeline() as pipe:
                pipe.zrem(GLOBAL_SLOTS, token).zrem(engine_slots, token).execute()


def circuit_key(engine_code: str) -> str:
    return f"i2v:worker:circuit:{engine_code}"


def circuit_is_open(engine_code: str) -> bool:
    if sync_redis.exists(circuit_key(engine_code)):
        return True
    health_key = f"i2v:engine:health:{engine_code}"
    if sync_redis.hget(health_key, "circuit_open") == "1":
        sync_redis.eval(
            "if redis.call('EXISTS', KEYS[1]) == 0 then redis.call('HSET', KEYS[2], 'circuit_open', '0') end",
            2,
            circuit_key(engine_code),
            health_key,
        )
    return False


def record_outcome(engine_code: str, success: bool, seconds: int, outage: bool = False) -> None:
    health_key = f"i2v:engine:health:{engine_code}"
    engine_samples = f"{health_key}:samples"
    sync_redis.eval(
        UPDATE_HEALTH,
        7,
        health_key,
        f"{health_key}:outcomes",
        "i2v:worker:cost_samples",
        f"{health_key}:outages",
        circuit_key(engine_code),
        "i2v:stats:avg_cost_sec",
        engine_samples,
        int(success),
        int(outage),
        seconds,
    )
