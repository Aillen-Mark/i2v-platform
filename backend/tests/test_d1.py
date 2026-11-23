"""D1 验收测试：认证、考生管理（Excel 导入/导出/密码条）、题目管理、交接契约 C 幂等。

全部连 Docker 里的真实 MySQL 8.0 / Redis 7 / MinIO，不使用 SQLite 或任何 Mock。
覆盖 prompts/01_base_D1.md 第三节列出的每一项。
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_init_pwd, generate_exam_password, hash_password
from app.core.timeutil import utcnow
from app.models import AuditLog, Candidate, Question, Quota, Work
from app.services import handoff, handoff_sync

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

AMBIGUOUS = set("O0Il1")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _xlsx(rows: list[tuple[str, str, str]]) -> bytes:
    """按模板生成导入文件：姓名 / 账号 / 备注。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["姓名", "账号", "备注"])
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _import(admin: AsyncClient, rows: list[tuple[str, str, str]]):
    return await admin.post(
        "/admin/candidates/import",
        files={"file": ("candidates.xlsx", _xlsx(rows), XLSX_MIME)},
    )


async def _create_candidate(admin: AsyncClient, name: str, account: str | None = None) -> dict:
    response = await admin.post("/admin/candidates", json={"name": name, "account": account, "remark": None})
    assert response.status_code == 200, response.text
    return response.json()


async def _count(model) -> int:
    async with AsyncSessionLocal() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


async def _candidate_by_account(account: str) -> Candidate | None:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(select(Candidate).where(Candidate.account == account))
        ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# 1. Excel 导入：100 人全部成功
# --------------------------------------------------------------------------- #
async def test_import_100_candidates_success(admin_client):
    rows = [(f"考生{i:03d}", f"S2026{i:04d}", "批量导入") for i in range(1, 101)]
    response = await _import(admin_client, rows)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_rows"] == 100
    assert body["success_count"] == 100
    assert body["failed_count"] == 0
    assert body["errors"] == []
    assert body["batch_no"]

    assert await _count(Candidate) == 100

    # 初始密码：8 位、含大小写与数字、无易混字符，且 Fernet 密文可解出同一密码
    async with AsyncSessionLocal() as session:
        candidates = (await session.execute(select(Candidate))).scalars().all()
    for candidate in candidates:
        assert candidate.password_hash.startswith("$2")
        assert candidate.password_hash != ""
        plain = decrypt_init_pwd(candidate.init_password_enc or "")
        assert plain is not None, f"{candidate.account} 的初始密码无法解密"
        assert len(plain) == 8
        assert any(c.isupper() for c in plain)
        assert any(c.islower() for c in plain)
        assert any(c.isdigit() for c in plain)
        assert not (set(plain) & AMBIGUOUS)
        assert candidate.init_pwd_expire_at is not None

    # 审计日志
    async with AsyncSessionLocal() as session:
        logs = (
            await session.execute(select(AuditLog).where(AuditLog.action == "import_users"))
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].after_val["success_count"] == 100
    assert logs[0].target_id == body["batch_no"]


# --------------------------------------------------------------------------- #
# 2. Excel 导入：按行号报错且有效行正常导入
# --------------------------------------------------------------------------- #
async def test_import_reports_row_errors_without_breaking_valid_rows(admin_client):
    await _create_candidate(admin_client, "已存在", "DUP001")

    rows = [
        ("", "A001", "空姓名"),  # 第 2 行：姓名缺失
        ("张三", "DUP001", "库内重复"),  # 第 3 行：账号已存在
        ("李四", "NEW001", "正常"),  # 第 4 行：成功
        ("王五", "NEW001", "文件内重复"),  # 第 5 行：与第 4 行重复
        ("赵六", "", "自动账号"),  # 第 6 行：账号留空 → 自动生成
    ]
    response = await _import(admin_client, rows)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_rows"] == 5
    assert body["success_count"] == 2
    assert body["failed_count"] == 3

    error_rows = {item["row"]: item["reason"] for item in body["errors"]}
    assert set(error_rows) == {2, 3, 5}
    assert "姓名" in error_rows[2]
    assert "已存在" in error_rows[3]
    assert "重复" in error_rows[5]

    # 有效行照常入库
    assert await _candidate_by_account("NEW001") is not None
    auto = await _candidate_by_account("C2026000001")
    assert auto is not None, "账号留空时应按 C2026+6 位序号规则自动生成"
    assert auto.name == "赵六"
    assert await _count(Candidate) == 3  # DUP001 + NEW001 + C2026000001


# --------------------------------------------------------------------------- #
# 3. Excel 导入：1000 行 ≤10 秒
# --------------------------------------------------------------------------- #
async def test_import_1000_rows_within_10_seconds(admin_client):
    rows = [(f"考生{i:04d}", f"B{i:06d}", "") for i in range(1, 1001)]

    started = time.perf_counter()
    response = await _import(admin_client, rows)
    wall_ms = int((time.perf_counter() - started) * 1000)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success_count"] == 1000, body
    assert body["failed_count"] == 0

    assert wall_ms <= 10_000, f"1000 行导入耗时 {wall_ms}ms，超过 10 秒"
    assert body["elapsed_ms"] <= 10_000, f"服务端统计 {body['elapsed_ms']}ms，超过 10 秒"
    assert await _count(Candidate) == 1000


# --------------------------------------------------------------------------- #
# 4. 密码规则
# --------------------------------------------------------------------------- #
async def test_generated_password_rules():
    samples = [generate_exam_password() for _ in range(500)]
    for password in samples:
        assert len(password) == 8
        assert any(c.isupper() for c in password)
        assert any(c.islower() for c in password)
        assert any(c.isdigit() for c in password)
        assert not (set(password) & AMBIGUOUS)


# --------------------------------------------------------------------------- #
# 5. 登录保护：连续 5 次失败锁定 5 分钟
# --------------------------------------------------------------------------- #
async def test_login_locked_after_five_failures(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "锁定测试", "LOCK001")
    client = candidate_client_factory()

    for attempt in range(1, 6):
        response = await client.post("/api/login", json={"account": "LOCK001", "password": "Wrong@1234"})
        if attempt < 5:
            assert response.status_code == 401, response.text
            assert response.json()["detail"]["remaining_attempts"] == 5 - attempt
        else:
            assert response.status_code == 429, response.text
            assert response.json()["code"] == "ACCOUNT_LOCKED"

    # 锁定期内即使密码正确也不放行
    response = await client.post("/api/login", json={"account": "LOCK001", "password": created["password"]})
    assert response.status_code == 429
    assert response.json()["detail"]["retry_after"] > 0

    # 把锁定时间改到过去，模拟 5 分钟到期后可以正常登录
    async with AsyncSessionLocal() as session:
        candidate = (
            await session.execute(select(Candidate).where(Candidate.account == "LOCK001"))
        ).scalar_one()
        candidate.locked_until = utcnow() - timedelta(seconds=1)
        await session.commit()

    response = await client.post("/api/login", json={"account": "LOCK001", "password": created["password"]})
    assert response.status_code == 200, response.text
    assert response.json()["account"] == "LOCK001"


# --------------------------------------------------------------------------- #
# 6. 同账号后登录踢掉前一个会话
# --------------------------------------------------------------------------- #
async def test_second_login_kicks_previous_session(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "会话测试", "SESS001")
    payload = {"account": "SESS001", "password": created["password"]}

    first = candidate_client_factory()
    second = candidate_client_factory()

    assert (await first.post("/api/login", json=payload)).status_code == 200
    assert (await first.get("/api/me")).status_code == 200

    assert (await second.post("/api/login", json=payload)).status_code == 200
    assert (await second.get("/api/me")).status_code == 200

    kicked = await first.get("/api/me")
    assert kicked.status_code == 401, f"旧会话应失效，实际 {kicked.status_code}"
    assert kicked.json()["code"] == "UNAUTHORIZED"


# --------------------------------------------------------------------------- #
# 7. 双端会话隔离
# --------------------------------------------------------------------------- #
async def test_candidate_cookie_cannot_access_admin_endpoints(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "隔离测试", "ISO001")
    client = candidate_client_factory()
    assert (
        await client.post("/api/login", json={"account": "ISO001", "password": created["password"]})
    ).status_code == 200

    for path in ("/admin/auth/me", "/admin/candidates", "/admin/questions"):
        response = await client.get(path)
        assert response.status_code == 401, f"{path} 应拒绝考生 Cookie，实际 {response.status_code}"

    # 反向：管理员 Cookie 不能访问考生端
    response = await admin_client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# --------------------------------------------------------------------------- #
# 8. 新增题目自动补建配额；3 道题总次数 = 6
# --------------------------------------------------------------------------- #
async def test_new_question_backfills_quotas(admin_client, candidate_client_factory):
    await _import(admin_client, [(f"考生{i}", f"Q{i:04d}", "") for i in range(1, 4)])
    assert await _count(Quota) == 0, "还没有题目时不应有配额行"

    question_ids: list[int] = []
    for index in range(1, 4):
        response = await admin_client.post(
            "/admin/questions",
            json={
                "title": f"题目{index}",
                "description": f"第 {index} 题：上传一张图生成 15 秒视频",
                "prompt_examples": ["示例A", "示例B", "示例C"],
                "default_quota": 2,
                "sort_order": index,
            },
        )
        assert response.status_code == 200, response.text
        question_ids.append(response.json()["id"])
        assert response.json()["code"] == f"Q{index}"

    # 每道题都为 3 名考生补建配额行
    assert await _count(Quota) == 9

    # 考生端：3 道题、每题剩余 2、总次数 6
    created = await _create_candidate(admin_client, "配额考生", "QUOTA01")
    assert await _count(Quota) == 12, "新增考生时应自动补齐所有启用题目的配额行"
    client = candidate_client_factory()
    assert (
        await client.post("/api/login", json={"account": "QUOTA01", "password": created["password"]})
    ).status_code == 200

    me = (await client.get("/api/me")).json()
    assert me["total_quota"] == 6
    assert me["total_remaining"] == 6

    questions = (await client.get("/api/questions")).json()
    assert len(questions) == 3
    for item in questions:
        assert item["quota_total"] == 2
        assert item["remaining"] == 2
        assert item["adjusted_flag"] is False
        assert item["prompt_examples"] == ["示例A", "示例B", "示例C"]

    # 软删除一道题（M2 起 DELETE 默认物理删除，显式 soft=true 才是停用）：
    # 考生端只剩 2 题、总次数 4，但配额行保留（可下载可统计）
    response = await admin_client.delete(f"/admin/questions/{question_ids[0]}", params={"soft": "true"})
    assert response.status_code == 200

    questions = (await client.get("/api/questions")).json()
    assert len(questions) == 2
    me = (await client.get("/api/me")).json()
    assert me["total_quota"] == 4
    assert await _count(Quota) == 12, "软删除题目不得删除已有配额行（可下载可统计）"


# --------------------------------------------------------------------------- #
# 9. 交接契约 C：重复调用只退还一次（异步 + 同步两套实现）
# --------------------------------------------------------------------------- #
async def _seed_work(quota_used: int = 1) -> dict[str, int | str]:
    suffix = uuid.uuid4().hex[:10]
    async with AsyncSessionLocal() as session:
        candidate = Candidate(
            account=f"c{suffix}",
            name="契约考生",
            password_hash=hash_password("Seed@1234"),
            status=1,
        )
        question = Question(
            code=f"Q{suffix}",
            title="契约题目",
            description="用于交接契约测试",
            default_quota=2,
            sort_order=0,
            status=1,
        )
        session.add_all([candidate, question])
        await session.flush()

        session.add(
            Quota(
                candidate_id=candidate.id,
                question_id=question.id,
                quota_total=2,
                quota_used=quota_used,
            )
        )
        work = Work(
            task_no=f"T{suffix}",
            candidate_id=candidate.id,
            question_id=question.id,
            seq=1,
            image_key="images/contract.jpg",
            prompt="一只橘猫在雪地里奔跑",
            task_status="queued",
            engine_code="wq-v1",
            submitted_at=utcnow(),
        )
        session.add(work)
        await session.flush()
        await session.commit()

        return {
            "work_id": work.id,
            "task_no": work.task_no,
            "candidate_id": candidate.id,
            "question_id": question.id,
        }


async def _quota_used(candidate_id: int, question_id: int) -> int:
    async with AsyncSessionLocal() as session:
        quota = (
            await session.execute(
                select(Quota).where(Quota.candidate_id == candidate_id, Quota.question_id == question_id)
            )
        ).scalar_one()
        return quota.quota_used


async def _refund_audit_count(task_no: str) -> int:
    async with AsyncSessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "refund_quota", AuditLog.target_id == task_no)
                )
            ).scalar()
            or 0
        )


async def test_contract_c_refund_is_idempotent_async():
    seeded = await _seed_work(quota_used=1)
    work_id = int(seeded["work_id"])

    for _ in range(3):
        await handoff.mark_work_failed_and_refund(
            work_id=work_id,
            error_code="UPSTREAM_TIMEOUT",
            readable_error_msg="模型响应超时，本次机会已退还",
            retry_count=1,
        )

    assert await _quota_used(int(seeded["candidate_id"]), int(seeded["question_id"])) == 0
    assert await _refund_audit_count(str(seeded["task_no"])) == 1

    async with AsyncSessionLocal() as session:
        work = await session.get(Work, work_id)
        assert work is not None
        assert work.task_status == "failed"
        assert work.quota_refunded == 1


async def test_contract_c_refund_is_idempotent_sync():
    seeded = await _seed_work(quota_used=1)
    work_id = int(seeded["work_id"])

    for _ in range(3):
        await asyncio.to_thread(
            handoff_sync.mark_work_failed_and_refund,
            work_id,
            "ENQUEUE_FAILED",
            "入队失败，本次机会已退还",
            1,
        )

    assert await _quota_used(int(seeded["candidate_id"]), int(seeded["question_id"])) == 0
    assert await _refund_audit_count(str(seeded["task_no"])) == 1

    async with AsyncSessionLocal() as session:
        work = await session.get(Work, work_id)
        assert work is not None
        assert work.task_status == "failed"
        assert work.error_code == "ENQUEUE_FAILED"
        assert work.quota_refunded == 1


# --------------------------------------------------------------------------- #
# 10. 导出与密码条
# --------------------------------------------------------------------------- #
async def test_export_and_password_slips(admin_client):
    await _import(admin_client, [("甲", "EXP001", ""), ("乙", "EXP002", "")])

    response = await admin_client.get("/admin/candidates/export", params={"format": "xlsx"})
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content))
    ws = wb.active
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    assert rows[0] == ("账号", "姓名", "初始密码", "状态", "备注")
    exported = {row[0]: row for row in rows[1:]}
    assert set(exported) == {"EXP001", "EXP002"}

    # 导出密码必须与库内初始密码一致
    for account, _name, password, status_text, _remark in rows[1:]:
        candidate = await _candidate_by_account(account)
        assert candidate is not None
        assert password == decrypt_init_pwd(candidate.init_password_enc or "")
        assert status_text == "启用"

    # 密码条 JSON 数据接口
    response = await admin_client.get("/admin/candidates/password-slips")
    assert response.status_code == 200
    slips = response.json()
    assert len(slips) == 2
    for slip in slips:
        assert slip["expired"] is False
        assert len(slip["password"]) == 8
        assert slip["expire_at"].endswith("+00:00"), "接口时间必须是 ISO 8601 带时区"

    # CSV 导出
    response = await admin_client.get("/admin/candidates/export", params={"format": "csv"})
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "EXP001" in text and "EXP002" in text


# --------------------------------------------------------------------------- #
# 11. 参考图上传 → MinIO → 考生端拿到可访问的签名链接
# --------------------------------------------------------------------------- #
PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\xdac\xfc\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d"
    b"\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def test_ref_image_upload_and_presigned_url(admin_client, candidate_client_factory):
    response = await admin_client.post(
        "/admin/questions",
        json={"title": "参考图题", "description": "带参考图", "default_quota": 2},
    )
    assert response.status_code == 200, response.text
    question_id = response.json()["id"]
    assert response.json()["ref_image_key"] is None

    response = await admin_client.post(
        f"/admin/questions/{question_id}/ref-image",
        files={"file": ("ref.png", PNG_1PX, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ref_image_key"] == "questions/Q1/ref.png"
    assert body["ref_image_url"].startswith("http://127.0.0.1:9000/")

    # 签名链接可直接下载（真实 MinIO，不是 Mock）
    # 注：本机环境变量 NO_PROXY 含 "[::1]"，httpx 读环境代理时会解析失败，故 trust_env=False
    async with AsyncClient(timeout=30.0, trust_env=False) as raw:
        fetched = await raw.get(body["ref_image_url"])
    assert fetched.status_code == 200
    assert fetched.content == PNG_1PX

    # 考生端题目列表里也带签名链接
    created = await _create_candidate(admin_client, "看图考生", "IMG001")
    client = candidate_client_factory()
    assert (
        await client.post("/api/login", json={"account": "IMG001", "password": created["password"]})
    ).status_code == 200
    questions = (await client.get("/api/questions")).json()
    assert len(questions) == 1
    assert questions[0]["ref_image_url"].startswith("http://127.0.0.1:9000/")

    # 非法类型被拒
    response = await admin_client.post(
        f"/admin/questions/{question_id}/ref-image",
        files={"file": ("evil.gif", b"GIF89a", "image/gif")},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_IMAGE_TYPE"


# --------------------------------------------------------------------------- #
# 12. 重置密码 / 批量启禁用 与审计
# --------------------------------------------------------------------------- #
async def test_reset_password_and_batch_status(admin_client, candidate_client_factory):
    first = await _create_candidate(admin_client, "重置甲", "RS001")
    second = await _create_candidate(admin_client, "重置乙", "RS002")

    client = candidate_client_factory()
    response = await admin_client.post(f"/admin/candidates/{first['id']}/reset-password")
    assert response.status_code == 200, response.text
    new_password = response.json()["password"]
    assert new_password != first["password"]

    # 旧密码失效、新密码可登录
    assert (
        await client.post("/api/login", json={"account": "RS001", "password": first["password"]})
    ).status_code == 401
    assert (
        await client.post("/api/login", json={"account": "RS001", "password": new_password})
    ).status_code == 200

    # 批量禁用 → 无法登录
    response = await admin_client.post(
        "/admin/candidates/batch-status",
        json={"ids": [first["id"], second["id"]], "status": 0},
    )
    assert response.status_code == 200, response.text
    response = await client.post("/api/login", json={"account": "RS001", "password": new_password})
    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_DISABLED"

    # 审计留痕：reset_pwd 与 toggle_status 各一条
    async with AsyncSessionLocal() as session:
        actions = (
            await session.execute(select(AuditLog.action))
        ).scalars().all()
    assert "reset_pwd" in actions
    assert "toggle_status" in actions
    assert "create_candidate" in actions
