"""M2 验收测试：双端隔离认证、Excel 1000 行极速导入、题目与配额联动。

全部走真实 HTTP 路由（httpx + ASGITransport）与 Docker 里的真实 MySQL 8.0 / Redis 7，
不使用 SQLite、不 Mock 任何 Session。

六大场景：
1. 双端隔离与防越权
2. 同账号互踢 + 5 次失败锁定
3. 1,005 行 Excel 极速导入（1000 有效 + 5 错误，硬断言 < 10 秒）
4. 新增第 4 题自动补配额（总次数 6 → 8）
5. 禁用 / 重置密码即时踢下线
6. 审计日志留痕
"""

from __future__ import annotations

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
from app.core.security import verify_password
from app.core.timeutil import utcnow
from app.models import AuditLog, Candidate, Question, Quota, Work

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMPORT_BUDGET_SECONDS = 10.0


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


async def _create_candidate(admin: AsyncClient, name: str, account: str) -> dict:
    response = await admin.post("/admin/candidates", json={"name": name, "account": account, "remark": None})
    assert response.status_code == 200, response.text
    return response.json()


async def _create_questions(admin: AsyncClient, count: int = 3, default_quota: int = 2) -> list[dict]:
    created: list[dict] = []
    for index in range(1, count + 1):
        response = await admin.post(
            "/admin/questions",
            json={
                "code": f"MQ{index}",
                "title": f"第 {index} 题",
                "description": f"第 {index} 题的创作要求",
                "prompt_examples": ["示例一", "示例二", "示例三"],
                "default_quota": default_quota,
                "sort_order": index,
                "status": 1,
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    return created


async def _count(model) -> int:
    async with AsyncSessionLocal() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


async def _candidate_id(account: str) -> int:
    async with AsyncSessionLocal() as session:
        found = (
            await session.execute(select(Candidate.id).where(Candidate.account == account))
        ).scalar_one_or_none()
    assert found is not None, f"考生 {account} 不存在"
    return int(found)


def _build_1005_rows(tag: str) -> tuple[list[tuple[str, str, str]], set[int]]:
    """构造 1,005 行数据：1,000 行有效（500 指定考号 + 500 留空）+ 5 行错误。

    行号从 2 起（第 1 行是表头）：
      第 2、3 行    —— DUPFILE01 / DUPFILE02 的首次出现（有效）
      第 4~501 行   —— 498 行指定考号（有效）
      第 502~1001 行 —— 500 行考号留空（自动生成，有效）
      第 1002、1003 行 —— 姓名为空（错误）
      第 1004 行    —— 账号与库中已有考生撞车（错误）
      第 1005、1006 行 —— 与第 2、3 行账号重复（错误）
    """
    rows: list[tuple[str, str, str]] = []

    rows.append((f"考生{tag}甲", "DUPFILE01", "文件内重复的首行"))
    rows.append((f"考生{tag}乙", "DUPFILE02", "文件内重复的首行"))
    for index in range(1, 499):
        rows.append((f"考生{tag}{index:04d}", f"T{tag}{index:05d}", "批量导入"))
    for index in range(1, 501):
        rows.append((f"自增{tag}{index:04d}", "", "账号留空自动生成"))

    rows.append(("", "E-EMPTY01", "姓名为空"))  # 第 1002 行
    rows.append(("", "E-EMPTY02", "姓名为空"))  # 第 1003 行
    rows.append((f"撞车{tag}", "EXIST0001", "库内已存在"))  # 第 1004 行
    rows.append((f"考生{tag}丙", "DUPFILE01", "文件内重复"))  # 第 1005 行
    rows.append((f"考生{tag}丁", "DUPFILE02", "文件内重复"))  # 第 1006 行

    assert len(rows) == 1005
    return rows, {1002, 1003, 1004, 1005, 1006}


async def _import_1000_and_assert(admin_client: AsyncClient, tag: str) -> dict:
    """执行 1,005 行导入并断言三条硬指标，返回响应体。"""
    rows, expected_error_rows = _build_1005_rows(tag)

    started = time.perf_counter()
    response = await _import(admin_client, rows)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    body = response.json()

    # 硬断言 1：端到端（含 3,000 条配额初始化）严格小于 10 秒
    print(f"\n[M2] 1,005 行 Excel 导入端到端耗时：{elapsed:.2f} 秒（服务端统计 {body['elapsed_ms']}ms）")
    assert elapsed < IMPORT_BUDGET_SECONDS, f"端到端 {elapsed:.2f}s 超过 10 秒红线"
    assert body["elapsed_ms"] < IMPORT_BUDGET_SECONDS * 1000

    # 硬断言 2：成功 1000 / 失败 5，且错误行号精确
    assert body["total_rows"] == 1005, body
    assert body["success_count"] == 1000, body
    assert body["failed_count"] == 5, body
    assert {item["row"] for item in body["errors"]} == expected_error_rows, body["errors"]

    assert await _count(Candidate) == 1001, "1000 名导入考生 + 1 名预置撞车考生"
    # 3 道启用题目：1000 名新考生 × 3 + 预置撞车考生 1 名 × 3 = 3003 条配额行
    assert await _count(Quota) == 3003, "导入后必须为每名考生补齐所有启用题目的配额行"
    return body


# --------------------------------------------------------------------------- #
# 场景 1：双端隔离与防越权
# --------------------------------------------------------------------------- #
async def test_dual_side_session_isolation(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "隔离考生", "ISO0001")

    candidate = candidate_client_factory()
    login = await candidate.post("/api/login", json={"account": "ISO0001", "password": created["password"]})
    assert login.status_code == 200, login.text
    assert "cand_sid" in candidate.cookies, "考生端必须使用 cand_sid Cookie"

    assert (await candidate.get("/api/me")).status_code == 200

    # 考生 Cookie 打管理端 → 401
    for path in ("/admin/candidates", "/admin/questions", "/admin/auth/me"):
        response = await candidate.get(path)
        assert response.status_code == 401, f"{path} 应拒绝考生 Cookie，实际 {response.status_code}"
    assert (await candidate.post("/admin/candidates/batch-status", json={"ids": [1], "status": 0})).status_code == 401

    # 管理员 Cookie 打管理端 → 200
    assert "admin_sid" in admin_client.cookies, "管理端必须使用 admin_sid Cookie"
    assert (await admin_client.get("/admin/candidates")).status_code == 200

    # 管理员 Cookie 打考生端 → 401（物理隔离是双向的）
    assert (await admin_client.get("/api/me")).status_code == 401

    # 两端 Cookie 名不同、互不污染
    assert "admin_sid" not in candidate.cookies
    assert "cand_sid" not in admin_client.cookies


# --------------------------------------------------------------------------- #
# 场景 2：同账号互踢 + 5 次失败锁定
# --------------------------------------------------------------------------- #
async def test_same_account_kick_out_and_lockout(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "互踢考生", "KICK0001")
    payload = {"account": "KICK0001", "password": created["password"]}

    first = candidate_client_factory()
    second = candidate_client_factory()

    assert (await first.post("/api/login", json=payload)).status_code == 200
    assert (await first.get("/api/me")).status_code == 200

    assert (await second.post("/api/login", json=payload)).status_code == 200
    assert (await second.get("/api/me")).status_code == 200, "后登录的会话必须有效"
    assert (await first.get("/api/me")).status_code == 401, "旧会话必须被新登录踢下线"

    # ---- 连续 5 次密码错误 → 锁定 ----
    locked = await _create_candidate(admin_client, "锁定考生", "LOCK0001")
    client = candidate_client_factory()
    wrong = {"account": "LOCK0001", "password": "Wrong@9999"}

    for attempt in range(1, 5):
        response = await client.post("/api/login", json=wrong)
        assert response.status_code == 401, f"第 {attempt} 次错误应 401，实际 {response.status_code}"

    fifth = await client.post("/api/login", json=wrong)
    assert fifth.status_code == 429, f"第 5 次错误应触发锁定（429），实际 {fifth.status_code}"

    # 锁定期内，即使密码正确也必须拒绝
    correct = await client.post("/api/login", json={"account": "LOCK0001", "password": locked["password"]})
    assert correct.status_code == 429, f"锁定期内正确密码也应 429，实际 {correct.status_code}"
    assert correct.json()["code"] == "ACCOUNT_LOCKED"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Candidate).where(Candidate.account == "LOCK0001"))
        ).scalar_one()
    assert row.locked_until is not None
    assert row.failed_login_count >= settings.LOGIN_MAX_FAILURES


# --------------------------------------------------------------------------- #
# 场景 3：1,005 行 Excel 极速导入 + 容错 + 登录配额校验
# --------------------------------------------------------------------------- #
async def test_excel_import_1000_rows_fast_and_login(admin_client, candidate_client_factory):
    tag = uuid.uuid4().hex[:4].upper()
    await _create_candidate(admin_client, "撞车考生", "EXIST0001")  # 供第 1004 行撞库
    await _create_questions(admin_client, count=3, default_quota=2)

    await _import_1000_and_assert(admin_client, tag)

    # 硬断言 3：拿导入考生的初始明文密码真实登录，并核对总配额 = 3 题 × 2 次 = 6
    async with AsyncSessionLocal() as session:
        auto_candidate = (
            await session.execute(
                select(Candidate).where(Candidate.account.like(f"{settings.AUTO_ACCOUNT_PREFIX}%")).limit(1)
            )
        ).scalar_one_or_none()
    assert auto_candidate is not None, "500 个留空考号应被自动生成"

    slip = await admin_client.get(f"/admin/candidates/{auto_candidate.id}/init-password")
    assert slip.status_code == 200, slip.text
    plain = slip.json()["password"]
    assert plain and len(plain) == 8
    assert verify_password(plain, auto_candidate.password_hash)

    client = candidate_client_factory()
    login = await client.post("/api/login", json={"account": auto_candidate.account, "password": plain})
    assert login.status_code == 200, login.text

    me = await client.get("/api/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["total_quota"] == 6, me_body
    assert me_body["total_used"] == 0
    assert me_body["total_remaining"] == 6
    assert me_body["in_window"] is False and me_body["next_window_start"] is None

    questions = await client.get("/api/questions")
    assert questions.status_code == 200
    assert len(questions.json()) == 3


# --------------------------------------------------------------------------- #
# 场景 4：新增第 4 题自动补配额（6 → 8）
# --------------------------------------------------------------------------- #
async def test_new_question_backfills_quota_for_all_1000(admin_client, candidate_client_factory):
    tag = uuid.uuid4().hex[:4].upper()
    await _create_candidate(admin_client, "撞车考生", "EXIST0001")
    await _create_questions(admin_client, count=3, default_quota=2)
    await _import_1000_and_assert(admin_client, tag)

    async with AsyncSessionLocal() as session:
        target = (
            await session.execute(
                select(Candidate).where(Candidate.account.like(f"{settings.AUTO_ACCOUNT_PREFIX}%")).limit(1)
            )
        ).scalar_one()
    slip = await admin_client.get(f"/admin/candidates/{target.id}/init-password")
    plain = slip.json()["password"]

    client = candidate_client_factory()
    assert (await client.post("/api/login", json={"account": target.account, "password": plain})).status_code == 200

    before = (await client.get("/api/me")).json()
    assert before["total_quota"] == 6

    # 新增第 4 题（default_quota=2）：必须在同一事务内为全体 1000 名考生补建配额
    created = await admin_client.post(
        "/admin/questions",
        json={
            "code": "MQ4",
            "title": "第 4 题",
            "description": "第 4 题的创作要求",
            "default_quota": 2,
            "sort_order": 4,
            "status": 1,
        },
    )
    assert created.status_code == 200, created.text

    # 全体考生都有第 4 题的配额行（零遗漏）
    async with AsyncSessionLocal() as session:
        question_id = int(created.json()["id"])
        rows = int(
            (
                await session.execute(
                    select(func.count()).select_from(Quota).where(Quota.question_id == question_id)
                )
            ).scalar()
            or 0
        )
        total_candidates = int(
            (await session.execute(select(func.count()).select_from(Candidate))).scalar() or 0
        )
    assert rows == total_candidates == 1001, f"第 4 题配额行 {rows} 条，考生 {total_candidates} 名"

    after = (await client.get("/api/me")).json()
    assert after["total_quota"] == 8, after
    assert after["total_remaining"] == 8

    questions = (await client.get("/api/questions")).json()
    assert len(questions) == 4
    assert [item["code"] for item in questions] == ["MQ1", "MQ2", "MQ3", "MQ4"]


# --------------------------------------------------------------------------- #
# 场景 5：禁用实时踢下线 + 重置密码踢下线
# --------------------------------------------------------------------------- #
async def test_disable_and_reset_password_kick_session(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "踢线考生", "KICK9001")
    candidate_id = created["id"]
    password = created["password"]

    client = candidate_client_factory()
    assert (await client.post("/api/login", json={"account": "KICK9001", "password": password})).status_code == 200
    assert (await client.get("/api/me")).status_code == 200

    # 禁用 → 原 Cookie 立即失效
    disable = await admin_client.post(
        "/admin/candidates/batch-status", json={"candidate_ids": [candidate_id], "status": 0}
    )
    assert disable.status_code == 200, disable.text
    kicked = await client.get("/api/me")
    assert kicked.status_code in (401, 403), f"禁用后应立即失效，实际 {kicked.status_code}"
    assert (await client.post("/api/login", json={"account": "KICK9001", "password": password})).status_code == 403

    # 重新启用并登录
    enable = await admin_client.post(
        "/admin/candidates/batch-status", json={"candidate_ids": [candidate_id], "status": 1}
    )
    assert enable.status_code == 200, enable.text
    assert (await client.post("/api/login", json={"account": "KICK9001", "password": password})).status_code == 200
    assert (await client.get("/api/me")).status_code == 200

    # 重置密码 → 原 Cookie 立即失效，新密码可登录
    reset = await admin_client.post(
        "/admin/candidates/batch-reset-password", json={"candidate_ids": [candidate_id]}
    )
    assert reset.status_code == 200, reset.text
    new_password = reset.json()["items"][0]["password"]
    assert new_password != password

    assert (await client.get("/api/me")).status_code == 401, "重置密码后旧会话必须立即失效"
    assert (await client.post("/api/login", json={"account": "KICK9001", "password": password})).status_code == 401
    assert (await client.post("/api/login", json={"account": "KICK9001", "password": new_password})).status_code == 200


# --------------------------------------------------------------------------- #
# 场景 6：审计日志留痕
# --------------------------------------------------------------------------- #
async def test_audit_logs_for_import_question_and_reset(admin_client, candidate_client_factory):
    # import_users
    response = await _import(admin_client, [("审计甲", "AUD0001", ""), ("审计乙", "", "自动账号")])
    assert response.status_code == 200, response.text
    assert response.json()["success_count"] == 2

    # create_question
    created = await admin_client.post(
        "/admin/questions",
        json={"code": "AUDQ1", "title": "审计题目", "description": "描述", "default_quota": 2, "status": 1},
    )
    assert created.status_code == 200, created.text

    # reset_pwd
    candidate_id = await _candidate_id("AUD0001")
    reset = await admin_client.post("/admin/candidates/batch-reset-password", json={"candidate_ids": [candidate_id]})
    assert reset.status_code == 200, reset.text

    async with AsyncSessionLocal() as session:
        logs = (await session.execute(select(AuditLog))).scalars().all()
    actions = [log.action for log in logs]
    assert "import_users" in actions, actions
    assert "create_question" in actions, actions
    assert "reset_pwd" in actions, actions

    import_log = next(log for log in logs if log.action == "import_users")
    assert import_log.after_val["success_count"] == 2
    assert import_log.admin_username == settings.ADMIN_INIT_USERNAME

    question_log = next(log for log in logs if log.action == "create_question")
    assert question_log.after_val["code"] == "AUDQ1"
    assert question_log.after_val["quota_rows_created"] == 2, "新增题目应为 2 名考生补建配额"


# --------------------------------------------------------------------------- #
# 附加：题目删除保护（有作品 → 409，无作品 → 物理删除并清理配额）
# --------------------------------------------------------------------------- #
async def test_question_delete_protection(admin_client):
    created = await _create_candidate(admin_client, "删除保护考生", "DEL0001")
    questions = await _create_questions(admin_client, count=2, default_quota=2)
    keep_id, drop_id = questions[0]["id"], questions[1]["id"]

    async with AsyncSessionLocal() as session:
        rows = int(
            (await session.execute(select(func.count()).select_from(Quota).where(Quota.question_id == drop_id))).scalar()
            or 0
        )
    assert rows == 1, "新增题目应为已有考生补建配额行"

    # 无作品 → 允许物理删除，并连带清理配额行
    deleted = await admin_client.delete(f"/admin/questions/{drop_id}")
    assert deleted.status_code == 200, deleted.text
    async with AsyncSessionLocal() as session:
        remaining = int(
            (await session.execute(select(func.count()).select_from(Quota).where(Quota.question_id == drop_id))).scalar()
            or 0
        )
    assert remaining == 0, "物理删除题目必须同时清理其配额行"

    # 造一条作品记录 → 物理删除被拒绝（409）
    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(
                Work(
                    task_no=f"DELPROT{uuid.uuid4().hex[:8]}",
                    candidate_id=int(created["id"]),
                    question_id=keep_id,
                    seq=1,
                    image_key="images/del-protect.jpg",
                    prompt="删除保护用作品",
                    task_status="succeeded",
                    engine_code="wq-v1",
                )
            )

    blocked = await admin_client.delete(f"/admin/questions/{keep_id}")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "QUESTION_HAS_WORKS"

    # 改用停用（软删除）即可
    soft = await admin_client.delete(f"/admin/questions/{keep_id}", params={"soft": "true"})
    assert soft.status_code == 200, soft.text
    async with AsyncSessionLocal() as session:
        question = await session.get(Question, keep_id)
    assert question is not None and question.status == 0


# --------------------------------------------------------------------------- #
# 附加：初始密码过期后单条查看返回 410 Gone
# --------------------------------------------------------------------------- #
async def test_init_password_expired_returns_410(admin_client):
    created = await _create_candidate(admin_client, "过期考生", "EXP9001")

    alive = await admin_client.get(f"/admin/candidates/{created['id']}/init-password")
    assert alive.status_code == 200, alive.text
    assert alive.json()["password"] == created["password"]

    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = await session.get(Candidate, int(created["id"]))
            row.init_pwd_expire_at = utcnow() - timedelta(minutes=1)

    gone = await admin_client.get(f"/admin/candidates/{created['id']}/init-password")
    assert gone.status_code == 410, gone.text
    assert gone.json()["code"] == "INIT_PASSWORD_EXPIRED"
