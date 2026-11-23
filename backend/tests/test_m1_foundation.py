"""阶段 1 验收测试：真实 MySQL 8.0 + Redis 7 + MinIO（无 SQLite、无 Session Mock）。

覆盖四项：
1. /healthz + 默认管理员 + 预置引擎 + MinIO 存储桶
2. 考试密码合规性（1000 次）与 bcrypt / Fernet 加解密
3. Mock 成功流转闭环（queued → running → succeeded，且出队）
4. 并发重复退还配额的幂等性（5 个并发只退 1 次）
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.core.security import (
    decrypt_init_pwd,
    encrypt_init_pwd,
    generate_exam_password,
    hash_password,
    verify_password,
)
from app.core.storage import list_buckets
from app.models import Admin, AuditLog, Candidate, Engine, Question, Quota, Work
from app.services.handoff import QUEUE_KEY, mark_work_failed_and_refund, trigger_video_generation

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

AMBIGUOUS = set("O0Il1")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_work(quota_used: int = 1, quota_total: int = 2) -> dict[str, int | str]:
    """在真实库插入 考生 / 题目 / 配额 / 一条 queued 作品。"""
    suffix = uuid.uuid4().hex[:12]
    async with AsyncSessionLocal() as session:
        async with session.begin():
            candidate = Candidate(
                account=f"exam_{suffix}",
                name="测试考生",
                password_hash=hash_password("Seed@1234"),
                status=1,
            )
            session.add(candidate)
            await session.flush()

            question = Question(
                code=f"Q{suffix[:8]}",
                title="测试题目",
                description="上传一张图并生成 15 秒视频",
                default_quota=quota_total,
                sort_order=0,
                status=1,
            )
            session.add(question)
            await session.flush()

            session.add(
                Quota(
                    candidate_id=candidate.id,
                    question_id=question.id,
                    quota_total=quota_total,
                    quota_used=quota_used,
                )
            )

            work = Work(
                task_no=f"T{suffix}",
                candidate_id=candidate.id,
                question_id=question.id,
                seq=1,
                image_key="images/test-seed.jpg",
                prompt="一只橘猫在雪地里奔跑",
                task_status="queued",
                engine_code="wq-v1",
                submitted_at=_utcnow(),
            )
            session.add(work)
            await session.flush()

            return {
                "work_id": work.id,
                "task_no": work.task_no,
                "candidate_id": candidate.id,
                "question_id": question.id,
            }


async def _wait_for_status(work_id: int, expected: str, timeout: float = 40.0) -> str | None:
    """轮询真实数据库等待作品进入目标状态。"""
    deadline = time.monotonic() + timeout
    current: str | None = None
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as session:
            current = await session.scalar(select(Work.task_status).where(Work.id == work_id))
        if current == expected:
            return current
        await asyncio.sleep(0.2)
    return current


# --------------------------------------------------------------------------- #
# 测试 1：健康检查、默认管理员、预置引擎、存储桶
# --------------------------------------------------------------------------- #
async def test_healthz_admin_engines_and_buckets(app_client):
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mysql": True, "redis": True}

    async with AsyncSessionLocal() as session:
        admin = (
            await session.execute(select(Admin).where(Admin.username == settings.ADMIN_INIT_USERNAME))
        ).scalar_one_or_none()
        assert admin is not None, "默认管理员未被初始化"
        assert admin.status == 1
        assert admin.password_hash != settings.admin_password, "管理员密码必须是 bcrypt 哈希而非明文"
        assert verify_password(settings.admin_password, admin.password_hash)

        engines = {row.code: row for row in (await session.execute(select(Engine))).scalars().all()}
        assert "wq-v1" in engines and "wq-v1-lite" in engines
        assert engines["wq-v1"].model_name == "MiniMax-H3-Highspeed"
        assert engines["wq-v1"].resolution == "768P"
        assert engines["wq-v1"].is_default == 1
        assert engines["wq-v1-lite"].model_name == "MiniMax-H3-Lite"
        assert engines["wq-v1-lite"].resolution == "480P"
        assert engines["wq-v1-lite"].is_default == 0

    buckets = await asyncio.to_thread(list_buckets)
    assert settings.MINIO_BUCKET_PUBLIC in buckets
    assert settings.MINIO_BUCKET_VIDEOS in buckets


# --------------------------------------------------------------------------- #
# 测试 2：密码合规性与加解密
# --------------------------------------------------------------------------- #
async def test_password_rules_and_crypto():
    samples = [generate_exam_password() for _ in range(1000)]
    for pwd in samples:
        assert len(pwd) == 8, f"长度不是 8：{pwd}"
        assert any(c.isupper() for c in pwd), f"缺少大写字母：{pwd}"
        assert any(c.islower() for c in pwd), f"缺少小写字母：{pwd}"
        assert any(c.isdigit() for c in pwd), f"缺少数字：{pwd}"
        assert not (set(pwd) & AMBIGUOUS), f"包含易混字符：{pwd}"

    plain = "Abc12345"
    hashed = hash_password(plain)
    assert hashed != plain
    assert hashed.startswith("$2")
    assert verify_password(plain, hashed) is True
    assert verify_password("Wrong9999", hashed) is False

    token = encrypt_init_pwd(plain)
    assert token != plain
    assert decrypt_init_pwd(token) == plain
    assert decrypt_init_pwd("not-a-valid-token") is None


# --------------------------------------------------------------------------- #
# 测试 3：Mock 成功流转闭环
# --------------------------------------------------------------------------- #
async def test_mock_success_flow(app_client):
    await redis_client.delete(QUEUE_KEY)
    seeded = await _seed_work(quota_used=1, quota_total=2)
    work_id = int(seeded["work_id"])

    await trigger_video_generation(
        work_id=work_id,
        image_public_url="http://127.0.0.1:9000/i2v-public/images/test-seed.jpg",
        prompt="一只橘猫在雪地里奔跑",
        engine_code="wq-v1",
        aspect_ratio="16:9",
        mock_force_outcome="succeed",
    )

    final_status = await _wait_for_status(work_id, "succeeded")
    assert final_status == "succeeded", f"作品未进入 succeeded，当前状态：{final_status}"

    async with AsyncSessionLocal() as session:
        work = await session.get(Work, work_id)
        assert work is not None
        assert work.task_status == "succeeded"
        assert work.video_key == f"videos/mock/{work.task_no}.mp4", "Mock 成片按 task_no 命名"
        assert work.file_size and work.file_size > 0
        assert work.duration_sec == 15
        assert work.cost_seconds is not None and work.cost_seconds >= 1
        assert work.upstream_task_id and work.upstream_task_id.startswith("mock-")
        assert work.finished_at is not None
        assert work.quota_refunded == 0, "成功出片不得退还配额"

        quota = (
            await session.execute(
                select(Quota).where(
                    Quota.candidate_id == int(seeded["candidate_id"]),
                    Quota.question_id == int(seeded["question_id"]),
                )
            )
        ).scalar_one()
        assert quota.quota_used == 1, "成功出片不应改变已用次数"

    score = await redis_client.zscore(QUEUE_KEY, str(work_id))
    assert score is None, "成功后必须已从排队 ZSet 移除"


# --------------------------------------------------------------------------- #
# 测试 4：并发重复退配额幂等（行锁）
# --------------------------------------------------------------------------- #
async def test_refund_is_idempotent_under_concurrency(app_client):
    await redis_client.delete(QUEUE_KEY)
    seeded = await _seed_work(quota_used=2, quota_total=2)
    work_id = int(seeded["work_id"])
    task_no = str(seeded["task_no"])

    await asyncio.gather(
        *[
            mark_work_failed_and_refund(
                work_id=work_id,
                error_code="UPSTREAM_TIMEOUT",
                readable_error_msg="模型响应超时，本次机会已退还",
                retry_count=1,
            )
            for _ in range(5)
        ]
    )

    async with AsyncSessionLocal() as session:
        quota = (
            await session.execute(
                select(Quota).where(
                    Quota.candidate_id == int(seeded["candidate_id"]),
                    Quota.question_id == int(seeded["question_id"]),
                )
            )
        ).scalar_one()
        assert quota.quota_used == 1, f"5 次并发退还只能退 1 次，实际 quota_used={quota.quota_used}"

        work = await session.get(Work, work_id)
        assert work is not None
        assert work.task_status == "failed"
        assert work.quota_refunded == 1
        assert work.error_code == "UPSTREAM_TIMEOUT"

        logs = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "refund_quota",
                    AuditLog.target_id == task_no,
                )
            )
        ).scalars().all()
        assert len(logs) == 1, f"退还审计日志必须严格 1 条，实际 {len(logs)} 条"
        assert logs[0].admin_id == 0
        assert logs[0].admin_username == "SYSTEM_WORKER"
        assert logs[0].target_type == "work"

    score = await redis_client.zscore(QUEUE_KEY, str(work_id))
    assert score is None, "失败后必须已从排队 ZSet 移除"
