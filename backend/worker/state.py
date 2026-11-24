"""Worker-side SQL and Redis state."""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from time import time
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import or_, select

from app.core.database import SyncSessionLocal
from app.models import Engine, GenerationAttempt, GenerationExecution, GenerationOutbox, Work
from app.services.execution_lease import ACTIVE_WORK, ExecutionStopped, database_now
from app.services.handoff_sync import sync_redis
from worker.config import worker_settings

logger = logging.getLogger(__name__)

TASK_SECONDS = 600
REDIS_CLAIM_SECONDS = 10
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

RENEW_SLOTS = """
for i = 1, 2 do
    local expiry = redis.call('ZSCORE', KEYS[i], ARGV[1])
    if not expiry or tonumber(expiry) <= tonumber(ARGV[2]) then return 0 end
end
for i = 1, 2 do
    redis.call('ZADD', KEYS[i], ARGV[3], ARGV[1])
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
    engine: Engine | None


def load_job(work_id: int) -> Job | None:
    with SyncSessionLocal() as session:
        row = session.execute(
            select(Work, Engine)
            .outerjoin(Engine, Engine.code == Work.engine_code)
            .where(Work.id == work_id, Work.task_status.in_(ACTIVE_WORK))
        ).one_or_none()
    return Job(*row) if row is not None else None


def stale_work_ids() -> list[int]:
    """Find received or legacy work eligible for a durable recovery claim."""
    attempt_status = (
        select(GenerationAttempt.status)
        .where(GenerationAttempt.work_id == Work.id)
        .order_by(GenerationAttempt.attempt_no.desc())
        .limit(1)
        .correlate(Work)
        .scalar_subquery()
    )
    with SyncSessionLocal() as session:
        now = database_now(session)
        return list(
            session.scalars(
                select(Work.id)
                .outerjoin(GenerationOutbox, GenerationOutbox.work_id == Work.id)
                .outerjoin(GenerationExecution, GenerationExecution.work_id == Work.id)
                .where(
                    Work.task_status.in_(ACTIVE_WORK),
                    or_(GenerationOutbox.work_id.is_(None), GenerationOutbox.received_at.is_not(None)),
                    or_(
                        attempt_status.is_(None),
                        attempt_status.not_in(("submission_unknown", "manual_review")),
                    ),
                    or_(
                        (GenerationExecution.work_id.is_(None))
                        & (Work.updated_at <= now - timedelta(seconds=120)),
                        (GenerationExecution.recover_after <= now)
                        & or_(
                            GenerationExecution.lease_expires_at.is_(None),
                            GenerationExecution.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(GenerationExecution.recover_after, Work.id)
                .limit(100)
            )
        )


def work_lock(work_id: int):
    return sync_redis.lock(f"i2v:worker:work:{work_id}", timeout=REDIS_CLAIM_SECONDS, blocking=False)


@contextmanager
def generation_slot(engine_code: str, engine_limit: int, account_limit: int):
    engine_slots = f"{GLOBAL_SLOTS}:{engine_code}"
    token = uuid4().hex
    now = time()
    duration = worker_settings().I2V_LEASE_SECONDS
    acquired = sync_redis.eval(
        ACQUIRE_SLOTS,
        2,
        GLOBAL_SLOTS,
        engine_slots,
        now,
        now + duration,
        token,
        account_limit,
        engine_limit,
    )

    def refresh():
        now = time()
        if not sync_redis.eval(RENEW_SLOTS, 2, GLOBAL_SLOTS, engine_slots, token, now, now + duration):
            raise ExecutionStopped("Generation concurrency slot expired")

    try:
        yield refresh if acquired else None
    finally:
        if acquired:
            try:
                with sync_redis.pipeline() as pipe:
                    pipe.zrem(GLOBAL_SLOTS, token).zrem(engine_slots, token).execute()
            except RedisError:
                logger.warning("Generation slot release failed engine=%s", engine_code)


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
