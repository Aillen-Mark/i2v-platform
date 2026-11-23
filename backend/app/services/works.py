"""考生创作与提交网关（M3 核心风控）。

- `create_draft`：校验题目/提示词/图片 → 上传 MinIO 公开桶 → 落 `works(status='draft')`
- `submit_work`：严格 5 步校验链，配额扣减只用**单条原子 UPDATE**，绝不「先查后改」
- `queue_ahead` / `estimate_wait`：排队位次与预计等待（Redis ZSet + 统计均值）
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redis import redis_client
from app.core.security import utcnow
from app.core.storage import presigned_get_url, upload_bytes
from app.core.timeutil import to_iso_utc
from app.models import Candidate, Question, Quota, Work
from app.services.engine import get_default_engine
from app.services.handoff import QUEUE_KEY, mark_work_failed_and_refund, trigger_video_generation
from app.services.moderation import moderate_image
from app.services.time_window import current_window_snapshot

logger = logging.getLogger(__name__)

settings = get_settings()

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PROMPT_LEN = 300
ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
AVG_COST_KEY = "i2v:stats:avg_cost_sec"
DEFAULT_AVG_COST_SEC = 120
DEFAULT_MAX_CONCURRENCY = 25
IMAGE_URL_TTL = 7200  # 契约 A 要求首帧链接有效期 ≥ 2 小时

_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
)


def detect_image(data: bytes) -> tuple[str, str] | None:
    """按魔数识别图片类型，返回 (扩展名, content-type)；不认识的返回 None。"""
    for magic, ext, mime in _IMAGE_SIGNATURES:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def new_task_no() -> str:
    """对外任务号：W + UTC 时间戳 + 6 位随机串（防遍历）。"""
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    return f"W{stamp}{secrets.token_hex(3).upper()}"


async def _quota_remaining(session: AsyncSession, candidate_id: int, question_id: int) -> int:
    row = (
        await session.execute(
            select(Quota.quota_total, Quota.quota_used).where(
                Quota.candidate_id == candidate_id, Quota.question_id == question_id
            )
        )
    ).one_or_none()
    if row is None:
        return 0
    return max(int(row[0]) - int(row[1]), 0)


async def queue_ahead(work_id: int) -> int:
    """ZRANK 得到的 0 基下标即「前面还有几人」；不在队列里按 0 处理。"""
    try:
        rank = await redis_client.zrank(QUEUE_KEY, str(work_id))
    except Exception:  # noqa: BLE001 - 排队信息属于展示增强，Redis 抖动不影响主流程
        logger.warning("读取排队位次失败：work_id=%s", work_id, exc_info=True)
        return 0
    return int(rank) if rank is not None and rank >= 0 else 0


async def average_cost_seconds() -> int:
    try:
        raw = await redis_client.get(AVG_COST_KEY)
    except Exception:  # noqa: BLE001
        return DEFAULT_AVG_COST_SEC
    if not raw:
        return DEFAULT_AVG_COST_SEC
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_AVG_COST_SEC
    return value if value > 0 else DEFAULT_AVG_COST_SEC


def estimate_wait_seconds(ahead: int, max_concurrency: int, avg_cost_sec: int) -> int:
    """预计等待 = ceil((前面人数 + 自己) / 并发上限) × 平均耗时。"""
    concurrency = max(int(max_concurrency or DEFAULT_MAX_CONCURRENCY), 1)
    avg_cost = max(int(avg_cost_sec or DEFAULT_AVG_COST_SEC), 1)
    return int(((ahead + 1) / concurrency) * avg_cost)


async def create_draft(
    session: AsyncSession,
    candidate: Candidate,
    *,
    question_id: int,
    prompt: str,
    aspect_ratio: str | None,
    data: bytes,
    filename: str,
    content_type: str | None,
) -> Work:
    """新建作品草稿：校验 → 审核 → 上传 → 落库。"""
    question = await session.get(Question, int(question_id))
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    if question.status != 1:
        raise AppError("QUESTION_DISABLED", "该题目已停用，无法提交", status_code=403)

    text = (prompt or "").strip()
    if not text:
        raise AppError("PROMPT_REQUIRED", "提示词不能为空", status_code=400)
    if len(text) > MAX_PROMPT_LEN:
        raise AppError("PROMPT_TOO_LONG", f"提示词不能超过 {MAX_PROMPT_LEN} 字", status_code=400)

    ratio = (aspect_ratio or "16:9").strip() or "16:9"
    if ratio not in ALLOWED_ASPECT_RATIOS:
        raise AppError(
            "INVALID_ASPECT_RATIO",
            f"画幅比例仅支持 {' / '.join(sorted(ALLOWED_ASPECT_RATIOS))}",
            status_code=400,
        )

    if not data:
        raise AppError("EMPTY_FILE", "上传图片为空", status_code=400)
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("FILE_TOO_LARGE", "图片不能超过 10MB", status_code=413)

    detected = detect_image(data)
    if detected is None:
        raise AppError(
            "UNSUPPORTED_IMAGE_TYPE",
            "仅支持 JPG / PNG / WEBP 格式（已按文件内容校验）",
            status_code=415,
            detail={"content_type": content_type},
        )
    ext, mime = detected

    verdict = await moderate_image(data, filename or f"upload{ext}", mime)
    if not verdict.passed:
        raise AppError("MODERATION_REJECTED", verdict.reason or "图片未通过内容审核", status_code=422)

    key = f"inputs/{candidate.account}/{uuid.uuid4().hex}{ext}"
    await asyncio.to_thread(upload_bytes, settings.MINIO_BUCKET_PUBLIC, key, data, mime)

    work = Work(
        task_no=new_task_no(),
        candidate_id=candidate.id,
        question_id=question.id,
        seq=None,
        image_key=key,
        aspect_ratio=ratio,
        prompt=text,
        task_status="draft",
    )
    session.add(work)
    await session.commit()
    await session.refresh(work)
    logger.info("考生 %s 新建草稿 %s（题目 %s）", candidate.account, work.task_no, question.code)
    return work


async def submit_work(session: AsyncSession, candidate: Candidate, work_id: int) -> dict[str, object]:
    """提交生成：5 步校验链 + 原子扣减 + 入队。返回给路由层组装响应的字典。"""
    # ---------- 第 1 步：归属 + 幂等（锁 work 行） ----------
    work = (
        await session.execute(
            select(Work).where(Work.id == int(work_id), Work.candidate_id == candidate.id).with_for_update()
        )
    ).scalar_one_or_none()
    if work is None:
        raise AppError("WORK_NOT_FOUND", "作品不存在或不属于当前考生", status_code=404)

    # 提前把标量取出来：后面一旦 rollback，ORM 实例会被 expire，
    # 在同步上下文里再访问属性会触发 lazy load（MissingGreenlet）。
    candidate_id = int(candidate.id)
    question_id = int(work.question_id)

    if work.task_status != "draft":
        # 已提交过：幂等返回当前状态，绝不再扣一次配额
        remaining = await _quota_remaining(session, candidate_id, question_id)
        ahead = await queue_ahead(work.id) if work.task_status == "queued" else 0
        engine = await get_default_engine(session)
        avg_cost = await average_cost_seconds()
        logger.info("作品 %s 重复提交，按幂等处理（status=%s）", work.id, work.task_status)
        return {
            "work": work,
            "idempotent": True,
            "quota_remaining": remaining,
            "queue_ahead": ahead,
            "estimated_wait_sec": estimate_wait_seconds(ahead, engine["max_concurrency"], avg_cost)
            if work.task_status == "queued"
            else None,
        }

    # ---------- 第 2 步：账号与题目状态 ----------
    if candidate.status != 1:
        raise AppError("ACCOUNT_DISABLED", "账号已被禁用，无法提交生成", status_code=403)
    question = await session.get(Question, work.question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    if question.status != 1:
        raise AppError("QUESTION_DISABLED", "该题目已停用，无法提交生成", status_code=403)

    # ---------- 第 3 步：开放时段（服务端强校验） ----------
    snapshot = await current_window_snapshot(session)
    if not snapshot["in_window"]:
        next_start = snapshot["next_window_start"]
        raise AppError(
            "OUT_OF_WINDOW",
            "当前不在开放时段内，暂不能提交生成"
            + (f"，下次开放时间 {to_iso_utc(next_start)}" if next_start else "，请等待管理员开放时段"),
            status_code=403,
            detail={
                "in_window": False,
                "next_window_start": to_iso_utc(next_start) if next_start else None,
            },
        )

    # ---------- 第 4 步：单条原子扣减（绝不做「先查余额再更新」） ----------
    decremented = await session.execute(
        update(Quota)
        .where(
            Quota.candidate_id == candidate_id,
            Quota.question_id == question_id,
            Quota.quota_used < Quota.quota_total,
        )
        .values(quota_used=Quota.quota_used + 1)
    )
    if decremented.rowcount != 1:
        await session.rollback()
        raise AppError(
            "QUOTA_EXHAUSTED",
            "本题生成次数已用完",
            status_code=409,
            detail={"candidate_id": candidate_id, "question_id": question_id},
        )

    # ---------- 第 5 步：序号 + 引擎 + 置 queued + 提交事务 ----------
    quota_row = (
        await session.execute(
            select(Quota.quota_used, Quota.quota_total).where(
                Quota.candidate_id == candidate_id, Quota.question_id == question_id
            )
        )
    ).one_or_none()
    quota_used = int(quota_row[0]) if quota_row else 0
    quota_total = int(quota_row[1]) if quota_row else 0
    engine = await get_default_engine(session)

    work.seq = int(quota_used or 0)
    work.task_status = "queued"
    work.engine_code = engine["code"]
    work.duration_sec = int(engine["duration"])
    work.submitted_at = utcnow()
    work.error_code = None
    work.error_msg = None
    await session.commit()

    # ---------- 事务提交之后才入队；入队失败立即退还，绝不吞次数 ----------
    try:
        image_url = await asyncio.to_thread(
            presigned_get_url, settings.MINIO_BUCKET_PUBLIC, work.image_key, IMAGE_URL_TTL
        )
        await trigger_video_generation(
            work_id=work.id,
            image_public_url=image_url,
            prompt=work.prompt,
            engine_code=engine["code"],
            aspect_ratio=work.aspect_ratio or "16:9",
        )
    except Exception:  # noqa: BLE001 - 入队异常必须回滚配额
        logger.exception("作品 %s 入队失败，立即退还配额", work.id)
        await mark_work_failed_and_refund(
            work_id=work.id,
            error_code="ENQUEUE_FAILED",
            readable_error_msg="任务入队失败，本次机会已退还，请重试",
            retry_count=0,
        )
        raise AppError(
            "ENQUEUE_FAILED",
            "任务入队失败，本次机会已退还，请稍后重试",
            status_code=503,
        ) from None

    ahead = await queue_ahead(work.id)
    avg_cost = await average_cost_seconds()
    remaining = max(quota_total - quota_used, 0)
    logger.info(
        "考生 %s 提交作品 %s（题目 %s，seq=%s，engine=%s，剩余 %s 次）",
        candidate.account,
        work.task_no,
        question.code,
        work.seq,
        engine["code"],
        remaining,
    )
    return {
        "work": work,
        "idempotent": False,
        "quota_remaining": remaining,
        "queue_ahead": ahead,
        "estimated_wait_sec": estimate_wait_seconds(ahead, engine["max_concurrency"], avg_cost),
    }
