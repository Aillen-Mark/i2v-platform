"""M3 验收测试：开放时段控制、配额精细调控、防超发提交网关。

全部走真实 HTTP 路由 + Docker 里的真实 MySQL 8.0 / Redis 7，不使用 SQLite 或任何 Mock Session。

七大场景：
1. 时段外拦截 / 时段内放行
2. 时段到期不影响已提交任务
3. 临时延长时段（含审计）
4. 配额调整与下调保护（409 二次确认）
5. 【生死线】20 并发抢最后 1 次配额不超发
6. 幂等防重提交
7. 失败自动退还全链路闭环
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.timeutil import utcnow
from app.models import AuditLog, Candidate, Question, Quota, TimeWindow, Work
from app.services.handoff import mark_work_failed_and_refund

pytestmark = pytest.mark.asyncio(loop_scope="session")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 1×1 透明 PNG（合法魔数，用于上传校验）
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _iso(value: datetime) -> str:
    """naive UTC → 带时区的 ISO 8601（接口统一口径）。"""
    return value.replace(tzinfo=timezone.utc).isoformat()


async def _create_candidate(admin: AsyncClient, name: str, account: str) -> dict:
    response = await admin.post("/admin/candidates", json={"name": name, "account": account})
    assert response.status_code == 200, response.text
    return response.json()


async def _create_question(admin: AsyncClient, code: str = "Q1", default_quota: int = 2) -> dict:
    response = await admin.post(
        "/admin/questions",
        json={
            "code": code,
            "title": f"题目 {code}",
            "description": "M3 测试题目",
            "prompt_examples": ["示例一", "示例二", "示例三"],
            "default_quota": default_quota,
            "sort_order": 1,
            "status": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_window(admin: AsyncClient, name: str, start: datetime, end: datetime, status: int = 1) -> dict:
    response = await admin.post(
        "/admin/time-windows",
        json={"name": name, "start_at": _iso(start), "end_at": _iso(end), "status": status},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _login_candidate(factory, account: str, password: str) -> AsyncClient:
    client = factory()
    response = await client.post("/api/login", json={"account": account, "password": password})
    assert response.status_code == 200, response.text
    return client


async def _upload_work(client: AsyncClient, question_id: int, prompt: str = "一只橘猫在雪地里奔跑") -> dict:
    response = await client.post(
        "/api/works",
        data={"question_id": str(question_id), "prompt": prompt, "aspect_ratio": "16:9"},
        files={"image": ("frame.png", PNG_1X1, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _quota(candidate_id: int, question_id: int) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Quota.quota_total, Quota.quota_used).where(
                    Quota.candidate_id == candidate_id, Quota.question_id == question_id
                )
            )
        ).one()
    return int(row[0]), int(row[1])


async def _set_quota(candidate_id: int, question_id: int, *, total: int | None = None, used: int | None = None) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            quota = (
                await session.execute(
                    select(Quota).where(Quota.candidate_id == candidate_id, Quota.question_id == question_id)
                )
            ).scalar_one()
            if total is not None:
                quota.quota_total = total
            if used is not None:
                quota.quota_used = used


async def _wait_status(work_id: int, expected: set[str], timeout: float = 30.0) -> str | None:
    deadline = time.monotonic() + timeout
    status: str | None = None
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as session:
            status = await session.scalar(select(Work.task_status).where(Work.id == work_id))
        if status in expected:
            return status
        await asyncio.sleep(0.2)
    return status


async def _audit_actions() -> list[str]:
    async with AsyncSessionLocal() as session:
        return list((await session.execute(select(AuditLog.action))).scalars().all())


# --------------------------------------------------------------------------- #
# 场景 1：时段外拦截 + 时段内放行
# --------------------------------------------------------------------------- #
async def test_gate_blocks_outside_window_and_allows_inside(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "时段考生", "WIN0001")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    client = await _login_candidate(candidate_client_factory, "WIN0001", created["password"])

    work = await _upload_work(client, question["id"])
    assert work["task_status"] == "draft"
    assert work["image_url"].startswith("http://127.0.0.1:9000/")

    # 非图片文件必须被魔数校验挡下
    bad = await client.post(
        "/api/works",
        data={"question_id": str(question["id"]), "prompt": "非法图片"},
        files={"image": ("fake.png", b"not-an-image-at-all", "image/png")},
    )
    assert bad.status_code == 415, bad.text

    # 无任何时段 → 403，且不扣配额
    blocked = await client.post(f"/api/works/{work['id']}/generate")
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["code"] == "OUT_OF_WINDOW"
    assert await _quota(created["id"], question["id"]) == (2, 0)

    # 只有一个已结束的历史时段 → 依然 403，且提示无未来时段
    now = utcnow()
    await _create_window(admin_client, "昨天上午场", now - timedelta(days=1, hours=2), now - timedelta(days=1))
    still_blocked = await client.post(f"/api/works/{work['id']}/generate")
    assert still_blocked.status_code == 403, still_blocked.text
    assert still_blocked.json()["detail"]["next_window_start"] is None
    assert await _quota(created["id"], question["id"]) == (2, 0)

    # 开启覆盖当前时间的时段 → 放行
    await _create_window(admin_client, "当前场", now - timedelta(minutes=10), now + timedelta(hours=1))
    me = (await client.get("/api/me")).json()
    assert me["in_window"] is True
    assert 0 < me["current_window_remaining_sec"] <= 3600 + 60

    submitted = await client.post(f"/api/works/{work['id']}/generate")
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["task_status"] == "queued"
    assert body["seq"] == 1
    assert body["quota_remaining"] == 1
    assert await _quota(created["id"], question["id"]) == (2, 1)


# --------------------------------------------------------------------------- #
# 场景 2：时段到期不影响已提交任务
# --------------------------------------------------------------------------- #
async def test_window_expiry_does_not_interrupt_submitted_task(admin_client, candidate_client_factory, monkeypatch):
    # Mock Worker 默认 80% 成功，本用例需要确定性出片
    monkeypatch.setattr(get_settings(), "MOCK_SUCCESS_RATE", 1.0)

    created = await _create_candidate(admin_client, "到期考生", "WIN0002")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    client = await _login_candidate(candidate_client_factory, "WIN0002", created["password"])

    now = utcnow()
    window = await _create_window(admin_client, "即将结束的场次", now - timedelta(minutes=30), now + timedelta(minutes=30))
    work = await _upload_work(client, question["id"])

    submitted = await client.post(f"/api/works/{work['id']}/generate")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["task_status"] == "queued"

    # 模拟考场时间结束：把时段改到过去
    closed = await admin_client.put(
        f"/admin/time-windows/{window['id']}",
        json={"end_at": _iso(now - timedelta(minutes=1))},
    )
    assert closed.status_code == 200, closed.text

    # 时段外不能再提交新任务
    another = await _upload_work(client, question["id"], "第二张图")
    rejected = await client.post(f"/api/works/{another['id']}/generate")
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "OUT_OF_WINDOW"

    # 但已提交的任务照常跑完
    final = await _wait_status(work["id"], {"succeeded", "failed"}, timeout=40)
    assert final == "succeeded", f"已提交任务不应被时段结束打断，实际状态 {final}"

    async with AsyncSessionLocal() as session:
        row = await session.get(Work, work["id"])
    assert row.video_key and row.video_key.startswith("videos/mock/")
    assert row.quota_refunded == 0


# --------------------------------------------------------------------------- #
# 场景 3：临时延长时段
# --------------------------------------------------------------------------- #
async def test_extend_time_window(admin_client):
    now = utcnow()
    window = await _create_window(admin_client, "待延长场次", now - timedelta(minutes=10), now + timedelta(minutes=20))

    # 未二次确认 → 400
    rejected = await admin_client.post(
        f"/admin/time-windows/{window['id']}/extend",
        json={"extend_minutes": 30, "confirm": False, "remark": "忘了确认"},
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "CONFIRM_REQUIRED"

    # 新结束时间必须晚于原结束时间
    earlier = await admin_client.post(
        f"/admin/time-windows/{window['id']}/extend",
        json={"new_end_at": _iso(now - timedelta(minutes=5)), "confirm": True},
    )
    assert earlier.status_code == 400, earlier.text

    # 正常延长 30 分钟
    extended = await admin_client.post(
        f"/admin/time-windows/{window['id']}/extend",
        json={"extend_minutes": 30, "confirm": True, "remark": "考场临时加时 30 分钟"},
    )
    assert extended.status_code == 200, extended.text
    body = extended.json()
    old_end = datetime.fromisoformat(window["end_at"])
    new_end = datetime.fromisoformat(body["end_at"])
    assert (new_end - old_end).total_seconds() == pytest.approx(1800, abs=1)

    async with AsyncSessionLocal() as session:
        logs = (
            await session.execute(select(AuditLog).where(AuditLog.action == "extend_window"))
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].before_val["end_at"] and logs[0].after_val["end_at"]
    assert logs[0].before_val["end_at"] != logs[0].after_val["end_at"]
    assert "加时" in (logs[0].remark or "")

    # 列表接口能标记当前生效时段
    listing = await admin_client.get("/admin/time-windows")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["active_window_id"] == window["id"]
    assert payload["items"][0]["is_active"] is True
    assert payload["items"][0]["remaining_sec"] > 0


# --------------------------------------------------------------------------- #
# 场景 4：配额调整与下调保护
# --------------------------------------------------------------------------- #
async def test_quota_adjust_and_downgrade_protection(admin_client):
    created = await _create_candidate(admin_client, "配额考生", "QTA0001")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    await _set_quota(created["id"], question["id"], total=2, used=2)

    # 按题批量 2 → 4
    raised = await admin_client.post(
        "/admin/quotas/batch-by-question",
        json={"question_id": question["id"], "quota_total": 4, "remark": "统一放宽到 4 次"},
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["updated"] == 1
    assert await _quota(created["id"], question["id"]) == (4, 2)

    # 下调到 1（已用 2 > 1）且未确认 → 409
    blocked = await admin_client.post(
        "/admin/quotas/batch-by-question",
        json={"question_id": question["id"], "quota_total": 1, "remark": "试试下调"},
    )
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()
    assert error["code"] == "QUOTA_DOWNGRADE_CONFIRM_REQUIRED"
    assert error["detail"]["affected_count"] == 1
    assert error["detail"]["samples"][0]["account"] == "QTA0001"
    assert await _quota(created["id"], question["id"]) == (4, 2), "被拦截时不得改动数据"

    # 二次确认后生效，并置 adjusted_flag = 1
    forced = await admin_client.post(
        "/admin/quotas/batch-by-question",
        json={"question_id": question["id"], "quota_total": 1, "force_confirm": True, "remark": "确认下调"},
    )
    assert forced.status_code == 200, forced.text
    assert await _quota(created["id"], question["id"]) == (1, 2)

    async with AsyncSessionLocal() as session:
        quota = (
            await session.execute(
                select(Quota).where(Quota.candidate_id == created["id"], Quota.question_id == question["id"])
            )
        ).scalar_one()
        logs = (
            await session.execute(select(AuditLog).where(AuditLog.action == "adjust_quota"))
        ).scalars().all()
    assert quota.adjusted_flag == 1
    assert len(logs) == 2
    assert logs[0].before_val["quota_total_distribution"] is not None
    assert logs[0].after_val["quota_total"] == 4

    # 按人设置：一键统一 + 逐题设置 + 多人加减
    uniform = await admin_client.put(
        f"/admin/quotas/by-candidate/{created['id']}",
        json={"uniform_quota": 3, "force_confirm": True, "remark": "统一 3 次"},
    )
    assert uniform.status_code == 200, uniform.text
    assert await _quota(created["id"], question["id"]) == (3, 2)

    per_item = await admin_client.put(
        f"/admin/quotas/by-candidate/{created['id']}",
        json={"items": [{"question_id": question["id"], "quota_total": 5}], "remark": "逐题 5 次"},
    )
    assert per_item.status_code == 200, per_item.text
    assert await _quota(created["id"], question["id"]) == (5, 2)

    delta = await admin_client.post(
        "/admin/quotas/batch-adjust",
        json={"candidate_ids": [created["id"]], "question_id": question["id"], "delta": -1, "remark": "减一次"},
    )
    assert delta.status_code == 200, delta.text
    assert await _quota(created["id"], question["id"]) == (4, 2)

    # 减到低于已用次数 → 409 保护
    over = await admin_client.post(
        "/admin/quotas/batch-adjust",
        json={"candidate_ids": [created["id"]], "question_id": question["id"], "delta": -3},
    )
    assert over.status_code == 409, over.text
    assert over.json()["detail"]["affected_count"] == 1

    # 明细查询
    listing = await admin_client.get("/admin/quotas", params={"keyword": "QTA0001"})
    assert listing.status_code == 200
    rows = listing.json()["items"]
    assert rows and rows[0]["quota_total"] == 4 and rows[0]["quota_used"] == 2
    assert rows[0]["quota_remaining"] == 2
    assert rows[0]["adjusted_flag"] is True


# --------------------------------------------------------------------------- #
# 场景 5：【生死线】20 并发抢最后 1 次配额
# --------------------------------------------------------------------------- #
async def test_concurrent_generate_never_overdraws(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "并发考生", "RACE0001")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    client = await _login_candidate(candidate_client_factory, "RACE0001", created["password"])

    now = utcnow()
    await _create_window(admin_client, "并发场次", now - timedelta(minutes=10), now + timedelta(hours=1))

    # 仅剩最后 1 次机会
    await _set_quota(created["id"], question["id"], total=2, used=1)

    work_ids = []
    for index in range(20):
        work = await _upload_work(client, question["id"], f"并发作品 {index}")
        work_ids.append(work["id"])
    assert len(set(work_ids)) == 20

    responses = await asyncio.gather(
        *[client.post(f"/api/works/{work_id}/generate") for work_id in work_ids],
        return_exceptions=True,
    )
    statuses = []
    for item in responses:
        assert not isinstance(item, Exception), f"并发请求抛异常：{item!r}"
        statuses.append(item.status_code)

    success = statuses.count(200)
    rejected = len([code for code in statuses if code in (409, 400)])
    print(f"\n[M3] 20 并发抢最后 1 次：200×{success} / 409×{statuses.count(409)} / 其他×{len(statuses) - success - rejected}")

    assert success == 1, f"必须严格只有 1 个成功，实际 {success}（状态码分布 {statuses}）"
    assert rejected == 19, f"其余 19 个必须被配额拦截，实际 {rejected}"
    assert statuses.count(409) == 19, f"拦截码应为 409，实际分布 {statuses}"

    total, used = await _quota(created["id"], question["id"])
    assert (total, used) == (2, 2), f"配额绝不能超发，实际 quota_used={used}"

    async with AsyncSessionLocal() as session:
        advanced = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Work)
                    .where(
                        Work.candidate_id == created["id"],
                        Work.question_id == question["id"],
                        Work.task_status.in_(["queued", "running", "succeeded"]),
                    )
                )
            ).scalar()
            or 0
        )
        drafts = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Work)
                    .where(
                        Work.candidate_id == created["id"],
                        Work.question_id == question["id"],
                        Work.task_status == "draft",
                    )
                )
            ).scalar()
            or 0
        )
    assert advanced == 1, f"只能有 1 条作品进入 queued/running/succeeded，实际 {advanced}"
    assert drafts == 19


# --------------------------------------------------------------------------- #
# 场景 6：幂等防重提交
# --------------------------------------------------------------------------- #
async def test_generate_is_idempotent(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "幂等考生", "IDEM0001")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    client = await _login_candidate(candidate_client_factory, "IDEM0001", created["password"])

    now = utcnow()
    await _create_window(admin_client, "幂等场次", now - timedelta(minutes=5), now + timedelta(hours=1))
    work = await _upload_work(client, question["id"])

    first = await client.post(f"/api/works/{work['id']}/generate")
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False
    assert await _quota(created["id"], question["id"]) == (2, 1)

    second = await client.post(f"/api/works/{work['id']}/generate")
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert second.json()["task_status"] == "queued"
    assert await _quota(created["id"], question["id"]) == (2, 1), "重复提交不得再扣配额"


# --------------------------------------------------------------------------- #
# 场景 7：失败自动退还全链路闭环
# --------------------------------------------------------------------------- #
async def test_failed_generation_refunds_and_can_retry(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "退还考生", "RFD0001")
    question = await _create_question(admin_client, "Q1", default_quota=2)
    client = await _login_candidate(candidate_client_factory, "RFD0001", created["password"])

    now = utcnow()
    await _create_window(admin_client, "退还场次", now - timedelta(minutes=5), now + timedelta(hours=1))

    work = await _upload_work(client, question["id"])
    submitted = await client.post(f"/api/works/{work['id']}/generate")
    assert submitted.status_code == 200, submitted.text
    assert await _quota(created["id"], question["id"]) == (2, 1)

    # 模拟上游失败回调
    await mark_work_failed_and_refund(
        work_id=work["id"],
        error_code="UPSTREAM_ERROR",
        readable_error_msg="模型生成失败，本次机会已退还，请重新提交",
        retry_count=1,
    )

    async with AsyncSessionLocal() as session:
        failed = await session.get(Work, work["id"])
    assert failed.task_status == "failed"
    assert failed.quota_refunded == 1
    assert failed.error_code == "UPSTREAM_ERROR"
    assert "已退还" in (failed.error_msg or "")
    assert await _quota(created["id"], question["id"]) == (2, 0), "失败必须退还次数"

    # 查询接口能看到失败原因
    detail = await client.get(f"/api/works/{work['id']}")
    assert detail.status_code == 200
    assert detail.json()["task_status"] == "failed"
    assert detail.json()["status_text"] == "失败"
    assert "已退还" in detail.json()["error_msg"]

    # 用退回的次数重新提交成功
    retry = await _upload_work(client, question["id"], "重试作品")
    again = await client.post(f"/api/works/{retry['id']}/generate")
    assert again.status_code == 200, again.text
    assert await _quota(created["id"], question["id"]) == (2, 1)

    actions = await _audit_actions()
    assert "refund_quota" in actions
