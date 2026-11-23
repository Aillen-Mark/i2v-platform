"""业务端 ↔ 生成模块 交接契约的**同步**实现（供同伴的 Celery Worker 直接调用）。

与 `app/services/handoff.py` 逻辑完全一致，只把 async 换成 sync：
- 契约 A（入队）：trigger_video_generation —— 业务端在事务 commit 之后调用；
  MOCK_WORKER=true 时用后台线程模拟「排队 → running → 成功/失败」，否则交给 Celery 任务。
- 契约 B（成功回调）：mark_work_succeeded。
- 契约 C（失败退还）：mark_work_failed_and_refund —— 单事务行锁 + 幂等退还。

数据库用 pymysql 同步驱动，Redis 用同步客户端；两套实现共用同一份测试用例。
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from importlib import import_module
from typing import Any

import redis as sync_redis_module
from redis.exceptions import RedisError
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import AuditLog, Quota, Work

logger = logging.getLogger(__name__)

settings = get_settings()

QUEUE_KEY = "i2v:queue:waiting_ids"

MOCK_OUTCOME_SUCCEED = "succeed"
MOCK_OUTCOME_FAIL = "fail"

# 铁律 8 的同步版：后台线程必须保存强引用，防止被回收
_MOCK_THREADS: set[threading.Thread] = set()

sync_redis: sync_redis_module.Redis = sync_redis_module.Redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    socket_connect_timeout=5,
    socket_keepalive=True,
)


def _celery_delay(
    work_id: int,
    image_url: str,
    prompt: str,
    engine_code: str,
    duration: int,
    aspect_ratio: str,
) -> None:
    """调用同伴提供的 Celery 任务（`<CELERY_TASK_MODULE>.<CELERY_TASK_FUNC>.delay(...)`）。"""
    module = import_module(settings.CELERY_TASK_MODULE)
    task = getattr(module, settings.CELERY_TASK_FUNC)
    task.delay(
        work_id=work_id,
        image_url=image_url,
        prompt=prompt,
        engine_code=engine_code,
        duration=duration,
        aspect_ratio=aspect_ratio,
    )


def trigger_video_generation(
    work_id: int,
    image_url: str,
    prompt: str,
    engine_code: str,
    duration: int = 15,
    aspect_ratio: str = "16:9",
    mock_force_outcome: str | None = None,
) -> None:
    """契约 A：入队。MOCK_WORKER=true 时用后台线程跑一次完整 Mock 流程。"""
    sync_redis.zadd(QUEUE_KEY, {str(work_id): time.time()})
    logger.info(
        "作品 %s 已入队（sync）：engine=%s aspect_ratio=%s prompt_len=%s",
        work_id,
        engine_code,
        aspect_ratio,
        len(prompt or ""),
    )

    if not settings.MOCK_WORKER:
        _celery_delay(work_id, image_url, prompt, engine_code, duration, aspect_ratio)
        return

    thread = threading.Thread(
        target=_run_mock_worker,
        kwargs={"work_id": work_id, "mock_force_outcome": mock_force_outcome},
        name=f"mock-worker-sync-{work_id}",
        daemon=True,
    )
    _MOCK_THREADS.add(thread)
    thread.start()


def _run_mock_worker(work_id: int, mock_force_outcome: str | None) -> None:
    started = time.monotonic()
    try:
        time.sleep(settings.MOCK_QUEUE_DELAY)
        sync_redis.zrem(QUEUE_KEY, str(work_id))
        _mark_running(work_id)

        time.sleep(settings.MOCK_RUN_DELAY)

        if mock_force_outcome in (MOCK_OUTCOME_SUCCEED, MOCK_OUTCOME_FAIL):
            outcome = mock_force_outcome
        else:
            outcome = MOCK_OUTCOME_SUCCEED if random.random() < settings.MOCK_SUCCESS_RATE else MOCK_OUTCOME_FAIL

        cost_seconds = max(1, int(round(time.monotonic() - started)))

        if outcome == MOCK_OUTCOME_SUCCEED:
            from app.services.handoff import _upload_mock_video

            video_key = f"videos/mock/{work_id}.mp4"
            uploaded = _upload_mock_video(video_key)
            mark_work_succeeded(
                work_id=work_id,
                video_key=video_key,
                file_size=uploaded or random.randint(6_000_000, 26_000_000),
                duration_sec=15,
                cost_seconds=cost_seconds,
                upstream_task_id=f"mock-{uuid.uuid4().hex[:24]}",
            )
        else:
            mark_work_failed_and_refund(
                work_id=work_id,
                error_code="UPSTREAM_ERROR",
                readable_error_msg="模型生成失败，本次机会已退还，请重新提交",
                retry_count=1,
            )
    except Exception:  # noqa: BLE001 - Mock 线程异常必须留痕
        logger.exception("Mock Worker（sync）处理作品 %s 失败", work_id)


def _mark_running(work_id: int) -> None:
    with SyncSessionLocal() as session:
        with session.begin():
            work = session.execute(select(Work).where(Work.id == work_id).with_for_update()).scalar_one_or_none()
            if work is None:
                logger.warning("作品 %s 不存在，跳过 running 状态更新", work_id)
                return
            if work.task_status not in ("queued", "draft"):
                logger.info("作品 %s 当前状态 %s，不再置 running", work_id, work.task_status)
                return
            work.task_status = "running"
            if not work.upstream_task_id:
                work.upstream_task_id = f"mock-{uuid.uuid4().hex[:24]}"


def _remove_from_wait_queue(work_id: int) -> None:
    try:
        sync_redis.zrem(QUEUE_KEY, str(work_id))
    except RedisError:
        logger.warning("清理作品排队标记失败 work_id=%s", work_id)


def mark_work_succeeded(
    work_id: int,
    video_key: str,
    file_size: int,
    duration_sec: int,
    cost_seconds: int,
    upstream_task_id: str,
) -> None:
    """契约 B：成功回调（幂等，仅 queued/running 可转 succeeded）。"""

    with SyncSessionLocal() as session:
        with session.begin():
            work = session.execute(select(Work).where(Work.id == work_id).with_for_update()).scalar_one_or_none()
            if work is None:
                logger.warning("成功回调：作品 %s 不存在，忽略", work_id)
                return
            if work.task_status not in ("queued", "running"):
                logger.info("成功回调：作品 %s 当前状态 %s，幂等忽略", work_id, work.task_status)
                return

            work.task_status = "succeeded"
            work.video_key = video_key
            work.file_size = file_size
            work.duration_sec = duration_sec
            work.cost_seconds = cost_seconds
            work.upstream_task_id = upstream_task_id
            work.finished_at = utcnow()
            work.error_code = None
            work.error_msg = None

    _remove_from_wait_queue(work_id)
    logger.info("作品 %s 生成成功（sync）：%s", work_id, video_key)


def mark_work_failed_and_refund(
    work_id: int,
    error_code: str,
    readable_error_msg: str,
    retry_count: int = 1,
) -> None:
    """契约 C：失败退还（幂等，重复调用只退一次）。"""

    with SyncSessionLocal() as session:
        with session.begin():
            work = session.execute(select(Work).where(Work.id == work_id).with_for_update()).scalar_one_or_none()
            if work is None:
                logger.warning("失败回调：作品 %s 不存在，忽略", work_id)
                return
            if work.quota_refunded == 1 or work.task_status not in ("queued", "running"):
                logger.info(
                    "失败回调：作品 %s 已退还或状态不允许退还（status=%s refunded=%s），幂等忽略",
                    work_id,
                    work.task_status,
                    work.quota_refunded,
                )
                return

            before_val: dict[str, Any] = {
                "task_status": work.task_status,
                "quota_refunded": work.quota_refunded,
                "retry_count": work.retry_count,
            }

            work.task_status = "failed"
            work.error_code = error_code
            work.error_msg = (readable_error_msg or "")[:255]
            work.retry_count = retry_count
            work.finished_at = utcnow()

            quota_row = session.execute(
                select(Quota)
                .where(Quota.candidate_id == work.candidate_id, Quota.question_id == work.question_id)
                .with_for_update()
            ).scalar_one_or_none()
            if quota_row is None:
                raise RuntimeError(
                    f"作品 {work_id} 退还配额失败：未找到配额行 "
                    f"(candidate_id={work.candidate_id}, question_id={work.question_id})"
                )

            session.execute(
                update(Quota)
                .where(Quota.candidate_id == work.candidate_id, Quota.question_id == work.question_id)
                .values(quota_used=func.greatest(Quota.quota_used - 1, 0))
            )
            work.quota_refunded = 1

            session.add(
                AuditLog(
                    admin_id=0,
                    admin_username="SYSTEM_WORKER",
                    action="refund_quota",
                    target_type="work",
                    target_id=work.task_no,
                    before_val=before_val,
                    after_val={"task_status": "failed", "quota_refunded": 1, "error_code": error_code},
                    remark=f"生成失败退还配额：{error_code}（task_no={work.task_no}）",
                )
            )

    _remove_from_wait_queue(work_id)
    logger.info("作品 %s 失败并已退还配额（sync）：%s %s", work_id, error_code, readable_error_msg)


def queue_position(work_id: int) -> int | None:
    """排队位次：0 表示队首，None 表示不在队列中。"""
    return sync_redis.zrank(QUEUE_KEY, str(work_id))
