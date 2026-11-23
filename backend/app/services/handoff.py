"""业务端 ↔ 生成模块 的唯一交接边界。

严格实现 docs/handoff-contract.md 的契约 A / B / C：
- 契约 A（入队）：trigger_video_generation —— 业务端在提交事务 commit 之后调用。
- 契约 B（成功回调）：mark_work_succeeded。
- 契约 C（失败退还）：mark_work_failed_and_refund —— 单事务内行锁 + 幂等退还。

真实 Worker（MiniMax + Celery + ffmpeg）由同伴实现，本文件不含任何上游调用。
本地开发 MOCK_WORKER=true 时，由本文件的 Mock 协程模拟「排队 → running → 成功/失败」。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.core.security import utcnow
from app.models import AuditLog, Quota, Work

logger = logging.getLogger(__name__)

settings = get_settings()

# Mock 出片用的合法最小 MP4 样本（1.1MB，H.264 + AAC，moov 前置便于边下边播）
MOCK_SAMPLE_PATH = Path(__file__).resolve().parents[1] / "assets" / "mock_sample.mp4"

# docs/handoff-contract.md §6：排队 ZSet，score 为入队时间戳
QUEUE_KEY = "i2v:queue:waiting_ids"

# 铁律：asyncio.create_task 创建的协程必须保存强引用，防止被 GC 回收
_MOCK_TASKS: set[asyncio.Task[None]] = set()

MOCK_OUTCOME_SUCCEED = "succeed"
MOCK_OUTCOME_FAIL = "fail"


async def trigger_video_generation(
    work_id: int,
    image_public_url: str,
    prompt: str,
    engine_code: str,
    aspect_ratio: str = "16:9",
    duration: int = 15,
    mock_force_outcome: str | None = None,
) -> None:
    """契约 A：把作品写入排队 ZSet，并驱动后续流程。

    - `MOCK_WORKER=true`：本地协程模拟「排队 → running → 真出片/失败」，写完对象存储再回调契约 B/C；
    - `MOCK_WORKER=false`：ZADD 之后把任务派发给同伴的 Celery
      （`CELERY_TASK_MODULE.CELERY_TASK_FUNC.delay(...)`，模块与函数名由 .env 配置）。
      派发异常会向上抛出，由网关捕获后立即 `mark_work_failed_and_refund(ENQUEUE_FAILED)`，绝不吞考生次数。
    """
    await redis_client.zadd(QUEUE_KEY, {str(work_id): time.time()})
    logger.info(
        "作品 %s 已入队：engine=%s aspect_ratio=%s prompt_len=%s image=%s",
        work_id,
        engine_code,
        aspect_ratio,
        len(prompt or ""),
        image_public_url,
    )

    if not settings.MOCK_WORKER:
        # 与 handoff_sync 共用同一份派发实现（同步 import_module + .delay）
        from app.services.handoff_sync import _celery_delay

        await asyncio.to_thread(
            _celery_delay, work_id, image_public_url, prompt, engine_code, duration, aspect_ratio
        )
        return

    task = asyncio.create_task(
        _run_mock_worker(work_id=work_id, mock_force_outcome=mock_force_outcome),
        name=f"mock-worker-{work_id}",
    )
    _MOCK_TASKS.add(task)
    task.add_done_callback(_MOCK_TASKS.discard)


async def _run_mock_worker(work_id: int, mock_force_outcome: str | None) -> None:
    """Mock 协程：短暂排队 → running → 按概率或指定结果回调契约 B / C。"""
    started = time.monotonic()
    try:
        await asyncio.sleep(settings.MOCK_QUEUE_DELAY)
        await redis_client.zrem(QUEUE_KEY, str(work_id))
        task_no = await _mark_running(work_id)

        await asyncio.sleep(settings.MOCK_RUN_DELAY)

        if mock_force_outcome in (MOCK_OUTCOME_SUCCEED, MOCK_OUTCOME_FAIL):
            outcome = mock_force_outcome
        else:
            outcome = (
                MOCK_OUTCOME_SUCCEED if random.random() < settings.MOCK_SUCCESS_RATE else MOCK_OUTCOME_FAIL
            )

        cost_seconds = max(1, int(round(time.monotonic() - started)))

        if outcome == MOCK_OUTCOME_SUCCEED:
            # 真出一片：把预置的合法 MP4 样本真实写入 MinIO，前端才能在线播放/下载
            video_key = f"videos/mock/{task_no or work_id}.mp4"
            uploaded = await asyncio.to_thread(_upload_mock_video, video_key)
            await mark_work_succeeded(
                work_id=work_id,
                video_key=video_key,
                file_size=uploaded or random.randint(6_000_000, 26_000_000),
                duration_sec=15,
                cost_seconds=cost_seconds,
                upstream_task_id=f"mock-{uuid.uuid4().hex[:24]}",
            )
        else:
            await mark_work_failed_and_refund(
                work_id=work_id,
                error_code="UPSTREAM_ERROR",
                readable_error_msg="模型生成失败，本次机会已退还，请重新提交",
                retry_count=1,
            )
    except Exception:  # noqa: BLE001 - Mock 协程异常不得静默，记录完整堆栈
        logger.exception("Mock Worker 处理作品 %s 失败", work_id)


def _upload_mock_video(video_key: str) -> int:
    """把 `app/assets/mock_sample.mp4` 上传到成片桶，返回字节数（失败返回 0）。"""
    from app.core.storage import upload_bytes

    if not MOCK_SAMPLE_PATH.exists():
        logger.warning("Mock 样本文件不存在：%s，跳过上传", MOCK_SAMPLE_PATH)
        return 0
    data = MOCK_SAMPLE_PATH.read_bytes()
    upload_bytes(settings.MINIO_BUCKET_VIDEOS, video_key, data, "video/mp4")
    logger.info("Mock 成片已写入对象存储：%s（%s 字节）", video_key, len(data))
    return len(data)


async def _mark_running(work_id: int) -> str | None:
    """把作品置为 running，返回 task_no（供 Mock 成片命名）。"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            work = (
                await session.execute(select(Work).where(Work.id == work_id).with_for_update())
            ).scalar_one_or_none()
            if work is None:
                logger.warning("作品 %s 不存在，跳过 running 状态更新", work_id)
                return None
            if work.task_status not in ("queued", "draft"):
                logger.info("作品 %s 当前状态 %s，不再置 running", work_id, work.task_status)
                return work.task_no
            work.task_status = "running"
            if not work.upstream_task_id:
                work.upstream_task_id = f"mock-{uuid.uuid4().hex[:24]}"
            return work.task_no


async def mark_work_succeeded(
    work_id: int,
    video_key: str,
    file_size: int,
    duration_sec: int,
    cost_seconds: int,
    upstream_task_id: str,
) -> None:
    """契约 B：成功回调。仅当状态为 queued/running 时更新，重复调用无副作用。"""
    await redis_client.zrem(QUEUE_KEY, str(work_id))

    async with AsyncSessionLocal() as session:
        async with session.begin():
            work = (
                await session.execute(select(Work).where(Work.id == work_id).with_for_update())
            ).scalar_one_or_none()
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

    logger.info("作品 %s 生成成功：%s", work_id, video_key)


async def mark_work_failed_and_refund(
    work_id: int,
    error_code: str,
    readable_error_msg: str,
    retry_count: int = 1,
) -> None:
    """契约 C：失败退还。单事务内行锁 + 幂等，重复调用只退还一次。"""
    await redis_client.zrem(QUEUE_KEY, str(work_id))

    async with AsyncSessionLocal() as session:
        async with session.begin():
            work = (
                await session.execute(select(Work).where(Work.id == work_id).with_for_update())
            ).scalar_one_or_none()
            if work is None:
                logger.warning("失败回调：作品 %s 不存在，忽略", work_id)
                return
            if work.quota_refunded == 1 or work.task_status == "succeeded":
                logger.info(
                    "失败回调：作品 %s 已退还或已成功（status=%s refunded=%s），幂等忽略",
                    work_id,
                    work.task_status,
                    work.quota_refunded,
                )
                return

            before_val = {
                "task_status": work.task_status,
                "quota_refunded": work.quota_refunded,
                "retry_count": work.retry_count,
            }

            work.task_status = "failed"
            work.error_code = error_code
            work.error_msg = (readable_error_msg or "")[:255]
            work.retry_count = retry_count
            work.finished_at = utcnow()

            refund_stmt = (
                update(Quota)
                .where(Quota.candidate_id == work.candidate_id, Quota.question_id == work.question_id)
                .values(quota_used=func.greatest(Quota.quota_used - 1, 0))
            )
            # 先锁定配额行：既保证「GREATEST 不为负」的语义，也暴露配额行缺失这种数据完整性异常
            quota_row = (
                await session.execute(
                    select(Quota)
                    .where(Quota.candidate_id == work.candidate_id, Quota.question_id == work.question_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if quota_row is None:
                raise RuntimeError(
                    f"作品 {work_id} 退还配额失败：未找到配额行 "
                    f"(candidate_id={work.candidate_id}, question_id={work.question_id})"
                )
            await session.execute(refund_stmt)

            work.quota_refunded = 1

            session.add(
                AuditLog(
                    admin_id=0,
                    admin_username="SYSTEM_WORKER",
                    action="refund_quota",
                    target_type="work",
                    target_id=work.task_no,
                    before_val=before_val,
                    after_val={
                        "task_status": "failed",
                        "quota_refunded": 1,
                        "error_code": error_code,
                    },
                    remark=f"生成失败退还配额：{error_code}（task_no={work.task_no}）",
                )
            )

    logger.info("作品 %s 失败并已退还配额：%s %s", work_id, error_code, readable_error_msg)


async def queue_position(work_id: int) -> int | None:
    """查询该作品前面还有多少个排队任务（0 表示队首，None 表示不在队列中）。"""
    rank = await redis_client.zrank(QUEUE_KEY, str(work_id))
    return rank


async def wait_mock_tasks(timeout: float = 15.0) -> int:
    """等待当前所有 Mock 协程结束，返回超时后仍未完成的数量。

    用途：测试收尾（避免 TRUNCATE 后自增 ID 复用造成串扰）与优雅停机。
    """
    pending = [task for task in list(_MOCK_TASKS) if not task.done()]
    if not pending:
        return 0
    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    return len(still_pending)
