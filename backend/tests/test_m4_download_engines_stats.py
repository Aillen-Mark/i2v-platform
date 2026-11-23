"""M4 验收测试：安全下载（30 分钟签名 + Range 断点续传）、流式 zip、模型管理台、
5 维度统计对账、CSV 导出、操作日志检索。

全部连真实 Docker MySQL / Redis / MinIO，不做任何 Mock。
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.storage import upload_bytes
from app.core.timeutil import utcnow
from app.models import Candidate, Engine, Question, Quota, Work

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 字节测试数据


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _iso(value) -> str:
    from datetime import timezone

    return value.replace(tzinfo=timezone.utc).isoformat()


async def _create_candidate(admin: AsyncClient, name: str, account: str) -> dict:
    response = await admin.post("/admin/candidates", json={"name": name, "account": account})
    assert response.status_code == 200, response.text
    return response.json()


async def _create_question(admin: AsyncClient, code: str, default_quota: int = 2) -> dict:
    response = await admin.post(
        "/admin/questions",
        json={
            "code": code,
            "title": f"题目 {code}",
            "description": "M4 测试题目",
            "default_quota": default_quota,
            "sort_order": 1,
            "status": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _login(factory, account: str, password: str) -> AsyncClient:
    client = factory()
    response = await client.post("/api/login", json={"account": account, "password": password})
    assert response.status_code == 200, response.text
    return client


async def _seed_work(
    *,
    candidate_id: int,
    question_id: int,
    task_status: str,
    engine_code: str | None = "wq-v1",
    seq: int = 1,
    video_key: str | None = None,
    duration_sec: int = 15,
    cost_seconds: int = 100,
    submitted_at=None,
) -> int:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            work = Work(
                task_no=f"M4{uuid.uuid4().hex[:16].upper()}",
                candidate_id=candidate_id,
                question_id=question_id,
                seq=seq,
                image_key="inputs/test/frame.png",
                prompt="M4 测试作品",
                task_status=task_status,
                engine_code=engine_code,
                video_key=video_key,
                duration_sec=duration_sec,
                cost_seconds=cost_seconds,
                submitted_at=submitted_at or utcnow(),
                finished_at=utcnow() if task_status in ("succeeded", "failed") else None,
                quota_refunded=1 if task_status == "failed" else 0,
            )
            session.add(work)
            await session.flush()
            return int(work.id)


def _put_video(key: str, data: bytes = VIDEO_BYTES) -> str:
    upload_bytes(settings.MINIO_BUCKET_VIDEOS, key, data, "video/mp4")
    return key


async def _engine_by_code(code: str) -> Engine:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(Engine).where(Engine.code == code))).scalar_one()


async def _restore_default_engine() -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            engines = (await session.execute(select(Engine))).scalars().all()
            for engine in engines:
                engine.is_default = 1 if engine.code == "wq-v1" else 0


# --------------------------------------------------------------------------- #
# 场景 1：安全下载 + 30 分钟签名 + Range 断点续传（206）
# --------------------------------------------------------------------------- #
async def test_secure_download_and_range_resume(admin_client, candidate_client_factory):
    alice = await _create_candidate(admin_client, "下载考生A", "DL0001")
    bob = await _create_candidate(admin_client, "下载考生B", "DL0002")
    question = await _create_question(admin_client, "Q1")

    key = _put_video(f"videos/test/{uuid.uuid4().hex}.mp4")
    work_id = await _seed_work(
        candidate_id=alice["id"],
        question_id=question["id"],
        task_status="succeeded",
        video_key=key,
        seq=1,
    )

    alice_client = await _login(candidate_client_factory, "DL0001", alice["password"])
    bob_client = await _login(candidate_client_factory, "DL0002", bob["password"])

    # 越权下载必须被拒绝
    forbidden = await bob_client.get(f"/api/works/{work_id}/download")
    assert forbidden.status_code in (403, 404), forbidden.text

    # 本人下载：文件名规范 + 1800 秒有效期
    response = await alice_client.get(f"/api/works/{work_id}/download")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expires_in"] == 1800
    assert body["filename"].startswith("DL0001_Q1_第1次_"), body["filename"]
    assert body["filename"].endswith(".mp4")
    assert body["file_size"] == len(VIDEO_BYTES)
    assert "X-Amz-Signature" in body["download_url"]
    # RFC 5987：中文文件名必须编码进 ResponseContentDisposition
    assert "response-content-disposition" in body["download_url"]
    assert "UTF-8" in body["download_url"]

    # 真实验证断点续传：Range 请求必须 206 + 1024 字节
    async with httpx.AsyncClient(trust_env=False, timeout=30.0) as raw:
        ranged = await raw.get(body["download_url"], headers={"Range": "bytes=0-1023"})
    print(
        f"\n[M4] Range 断点续传：status={ranged.status_code} "
        f"len={len(ranged.content)} content-range={ranged.headers.get('content-range')}"
    )
    assert ranged.status_code == 206, f"应返回 206 Partial Content，实际 {ranged.status_code}"
    assert len(ranged.content) == 1024, f"应返回 1024 字节，实际 {len(ranged.content)}"
    assert ranged.content == VIDEO_BYTES[:1024]

    # 302 跳转模式（ASGI 客户端，不跟随跳转）
    redirect = await alice_client.get(
        f"/api/works/{work_id}/download", params={"redirect": "1"}, follow_redirects=False
    )
    assert redirect.status_code in (302, 307), redirect.status_code
    assert "X-Amz-Signature" in redirect.headers["location"]

    # 未出片作品不能下载
    draft_id = await _seed_work(
        candidate_id=alice["id"], question_id=question["id"], task_status="draft", video_key=None
    )
    not_ready = await alice_client.get(f"/api/works/{draft_id}/download")
    assert not_ready.status_code == 409, not_ready.text

    # 对象不存在（Mock 占位）时给明确 404
    ghost_id = await _seed_work(
        candidate_id=alice["id"],
        question_id=question["id"],
        task_status="succeeded",
        video_key="videos/mock/does-not-exist.mp4",
    )
    ghost = await alice_client.get(f"/api/works/{ghost_id}/download")
    assert ghost.status_code == 404, ghost.text
    assert ghost.json()["code"] == "VIDEO_OBJECT_MISSING"


# --------------------------------------------------------------------------- #
# 场景 2：流式 zip 打包下载
# --------------------------------------------------------------------------- #
async def test_streaming_zip_download(admin_client, candidate_client_factory):
    created = await _create_candidate(admin_client, "打包考生", "ZIP0001")
    question = await _create_question(admin_client, "Q1")

    payloads = {
        f"videos/test/{uuid.uuid4().hex}.mp4": b"A" * 5000,
        f"videos/test/{uuid.uuid4().hex}.mp4": b"B" * 7000,
    }
    for index, (key, data) in enumerate(payloads.items(), start=1):
        _put_video(key, data)
        await _seed_work(
            candidate_id=created["id"],
            question_id=question["id"],
            task_status="succeeded",
            video_key=key,
            seq=index,
        )

    client = await _login(candidate_client_factory, "ZIP0001", created["password"])

    # 作品列表
    listing = await client.get("/api/me/works")
    assert listing.status_code == 200, listing.text
    listed = listing.json()
    assert listed["total"] == 2
    assert len(listed["groups"]) == 1
    assert listed["groups"][0]["question_code"] == "Q1"
    assert all(item["download_ready"] for item in listed["items"])

    # 流式 zip
    response = await client.get("/api/me/download-all")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    print(f"\n[M4] zip 内文件：{names}")
    assert len(names) == 2
    assert all(name.startswith("ZIP0001_Q1_第") and name.endswith(".mp4") for name in names)
    sizes = sorted(len(archive.read(name)) for name in names)
    assert sizes == [5000, 7000], f"解压后字节数应与入桶数据一致，实际 {sizes}"

    # 没有任何成功作品时返回 400
    empty = await _create_candidate(admin_client, "空考生", "ZIP0002")
    empty_client = await _login(candidate_client_factory, "ZIP0002", empty["password"])
    none = await empty_client.get("/api/me/download-all")
    assert none.status_code == 400, none.text
    assert none.json()["code"] == "NO_VIDEO_TO_DOWNLOAD"


# --------------------------------------------------------------------------- #
# 场景 3：模型配置 + 一键切换 480P 降级引擎实时生效
# --------------------------------------------------------------------------- #
async def test_engine_switch_takes_effect_immediately(admin_client, candidate_client_factory):
    lite = await _engine_by_code("wq-v1-lite")
    main = await _engine_by_code("wq-v1")
    try:
        created = await _create_candidate(admin_client, "引擎考生", "ENG0001")
        question = await _create_question(admin_client, "Q1", default_quota=3)
        client = await _login(candidate_client_factory, "ENG0001", created["password"])

        now = utcnow()
        window = await admin_client.post(
            "/admin/time-windows",
            json={
                "name": "引擎场次",
                "start_at": _iso(now - timedelta(minutes=5)),
                "end_at": _iso(now + timedelta(hours=1)),
                "status": 1,
            },
        )
        assert window.status_code == 200, window.text

        # 一键切换到 480P 降级引擎
        switched = await admin_client.post(f"/admin/engines/{lite.id}/set-default")
        assert switched.status_code == 200, switched.text
        assert switched.json()["code"] == "wq-v1-lite"
        assert switched.json()["is_default"] is True

        # 考生立刻提交 → 必须使用新默认引擎
        upload = await client.post(
            "/api/works",
            data={"question_id": str(question["id"]), "prompt": "引擎切换测试"},
            files={"image": ("frame.png", PNG_1X1, "image/png")},
        )
        assert upload.status_code == 200, upload.text
        work_id = upload.json()["id"]
        submitted = await client.post(f"/api/works/{work_id}/generate")
        assert submitted.status_code == 200, submitted.text

        async with AsyncSessionLocal() as session:
            work = await session.get(Work, work_id)
        assert work.engine_code == "wq-v1-lite", f"应立即使用 480P 引擎，实际 {work.engine_code}"

        # 引擎列表能看到 480P 为默认
        listing = await admin_client.get("/admin/engines")
        assert listing.status_code == 200
        engines = {item["code"]: item for item in listing.json()}
        assert engines["wq-v1-lite"]["is_default"] is True
        assert engines["wq-v1"]["is_default"] is False

        # 禁止停用当前默认引擎
        blocked = await admin_client.put(f"/admin/engines/{lite.id}", json={"status": 0})
        assert blocked.status_code == 400, blocked.text
        assert blocked.json()["code"] == "ENGINE_DEFAULT_CANNOT_DISABLE"

        # 编辑单价 / 并发上限 → 落库并写审计
        updated = await admin_client.put(
            f"/admin/engines/{lite.id}",
            json={"price_per_sec": "0.2", "max_concurrency": 8, "name": "极速备用引擎(480P)"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["max_concurrency"] == 8
        assert Decimal(str(updated.json()["price_per_sec"])) == Decimal("0.2")
    finally:
        await _restore_default_engine()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                lite_row = await session.get(Engine, lite.id)
                lite_row.max_concurrency = 25
                main_row = await session.get(Engine, main.id)
                main_row.price_per_sec = Decimal("0")


# --------------------------------------------------------------------------- #
# 场景 4：5 维度统计零误差对账
# --------------------------------------------------------------------------- #
async def test_five_dimension_stats_reconcile(admin_client):
    # 固定单价：wq-v1 = 0.5 元/秒，wq-v1-lite = 0.2 元/秒
    main = await _engine_by_code("wq-v1")
    lite = await _engine_by_code("wq-v1-lite")
    assert (await admin_client.put(f"/admin/engines/{main.id}", json={"price_per_sec": "0.5"})).status_code == 200
    assert (await admin_client.put(f"/admin/engines/{lite.id}", json={"price_per_sec": "0.2"})).status_code == 200

    alice = await _create_candidate(admin_client, "统计甲", "ST0001")
    bob = await _create_candidate(admin_client, "统计乙", "ST0002")
    q1 = await _create_question(admin_client, "Q1")
    q2 = await _create_question(admin_client, "Q2")

    # 3 成功 + 1 失败；成功：wq-v1 ×2（15s）、wq-v1-lite ×1（15s）
    await _seed_work(candidate_id=alice["id"], question_id=q1["id"], task_status="succeeded",
                     engine_code="wq-v1", seq=1, duration_sec=15, cost_seconds=100)
    await _seed_work(candidate_id=alice["id"], question_id=q1["id"], task_status="succeeded",
                     engine_code="wq-v1", seq=2, duration_sec=15, cost_seconds=120)
    await _seed_work(candidate_id=bob["id"], question_id=q2["id"], task_status="succeeded",
                     engine_code="wq-v1-lite", seq=1, duration_sec=15, cost_seconds=80)
    await _seed_work(candidate_id=bob["id"], question_id=q1["id"], task_status="failed",
                     engine_code="wq-v1", seq=1, duration_sec=15, cost_seconds=200)

    overview = await admin_client.get("/admin/stats/overview")
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["candidates_total"] == 2
    assert data["works_total"] == 4
    assert data["succeeded"] == 3
    assert data["failed"] == 1
    assert data["pending"] == 0
    assert data["success_rate"] == 75.0
    assert data["total_duration_sec"] == 45
    assert Decimal(str(data["estimated_cost"])) == Decimal("18.0000"), data["estimated_cost"]

    by_question = (await admin_client.get("/admin/stats/by-question")).json()
    stats = {item["code"]: item for item in by_question}
    assert stats["Q1"]["total"] == 3 and stats["Q1"]["succeeded"] == 2 and stats["Q1"]["failed"] == 1
    assert stats["Q1"]["fail_rate"] == 33.33
    assert stats["Q1"]["avg_cost_sec"] == pytest.approx((100 + 120 + 200) / 3, abs=0.01)
    assert stats["Q2"]["total"] == 1 and stats["Q2"]["failed"] == 0

    by_engine = (await admin_client.get("/admin/stats/by-engine")).json()
    engines = {item["code"]: item for item in by_engine}
    assert engines["wq-v1"]["calls"] == 3
    assert engines["wq-v1"]["succeeded"] == 2
    assert engines["wq-v1"]["total_duration_sec"] == 30
    assert engines["wq-v1"]["share_rate"] == 75.0
    assert Decimal(str(engines["wq-v1"]["cost"])) == Decimal("15.0000")
    assert engines["wq-v1-lite"]["calls"] == 1
    assert engines["wq-v1-lite"]["total_duration_sec"] == 15
    assert engines["wq-v1-lite"]["share_rate"] == 25.0
    assert Decimal(str(engines["wq-v1-lite"]["cost"])) == Decimal("3.0000")

    by_candidate = (await admin_client.get("/admin/stats/by-candidate")).json()
    people = {item["account"]: item for item in by_candidate["items"]}
    assert by_candidate["total"] == 2
    assert people["ST0001"]["success_count"] == 2 and people["ST0001"]["failed_count"] == 0
    assert people["ST0002"]["success_count"] == 1 and people["ST0002"]["failed_count"] == 1
    assert people["ST0001"]["last_active_at"] is not None

    by_day = (await admin_client.get("/admin/stats/by-day")).json()
    assert len(by_day) == 1
    assert by_day[0]["total"] == 4
    assert by_day[0]["succeeded"] == 3
    assert by_day[0]["failed"] == 1
    assert by_day[0]["fail_rate"] == 25.0


# --------------------------------------------------------------------------- #
# 场景 5：CSV 导出（UTF-8 BOM）与成片链接清单
# --------------------------------------------------------------------------- #
async def test_csv_export_bom_and_video_links(admin_client):
    created = await _create_candidate(admin_client, "导出考生", "EXP0001")
    question = await _create_question(admin_client, "Q1")
    key = _put_video(f"videos/test/{uuid.uuid4().hex}.mp4")
    await _seed_work(
        candidate_id=created["id"],
        question_id=question["id"],
        task_status="succeeded",
        video_key=key,
        seq=1,
    )

    works_csv = await admin_client.get("/admin/stats/export-csv", params={"dimension": "works"})
    assert works_csv.status_code == 200
    raw = works_csv.content
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须以 UTF-8 BOM 开头"
    text = raw.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].startswith("作品ID,任务号,考生账号")
    assert len(lines) == 2, f"表头 + 1 条作品，实际 {len(lines)} 行"
    assert "EXP0001" in lines[1]

    candidates_csv = await admin_client.get("/admin/stats/export-csv", params={"dimension": "candidates"})
    assert candidates_csv.content.startswith(b"\xef\xbb\xbf")
    c_lines = [line for line in candidates_csv.content.decode("utf-8-sig").splitlines() if line.strip()]
    assert c_lines[0].startswith("考生账号,考生姓名,状态")
    assert len(c_lines) == 2

    links = await admin_client.get("/admin/stats/export-video-links", params={"candidate_id": created["id"]})
    assert links.status_code == 200, links.text
    items = links.json()
    assert len(items) == 1
    assert items[0]["filename"].startswith("EXP0001_Q1_第1次_")
    assert items[0]["expires_in"] == 1800
    assert "X-Amz-Signature" in items[0]["download_url"]

    links_csv = await admin_client.get(
        "/admin/stats/export-video-links", params={"candidate_id": created["id"], "format": "csv"}
    )
    assert links_csv.status_code == 200
    assert links_csv.content.startswith(b"\xef\xbb\xbf")
    assert "规范文件名" in links_csv.content.decode("utf-8-sig")


# --------------------------------------------------------------------------- #
# 场景 6：审计日志多条件检索
# --------------------------------------------------------------------------- #
async def test_audit_log_search(admin_client):
    created = await _create_candidate(admin_client, "审计考生", "AUD9001")
    question = await _create_question(admin_client, "Q1")

    before = utcnow() - timedelta(minutes=1)
    assert (
        await admin_client.post(
            "/admin/quotas/batch-by-question",
            json={"question_id": question["id"], "quota_total": 4, "remark": "审计检索用"},
        )
    ).status_code == 200
    lite = await _engine_by_code("wq-v1-lite")
    assert (await admin_client.post(f"/admin/engines/{lite.id}/set-default")).status_code == 200

    try:
        by_action = await admin_client.get("/admin/audit-logs", params={"action": "adjust_quota"})
        assert by_action.status_code == 200, by_action.text
        payload = by_action.json()
        assert payload["total"] >= 1
        assert all(item["action"] == "adjust_quota" for item in payload["items"])
        assert payload["items"][0]["before_val"] is not None

        by_switch = await admin_client.get("/admin/audit-logs", params={"action": "switch_default_engine"})
        assert by_switch.json()["total"] >= 1
        assert by_switch.json()["items"][0]["after_val"]["default"] == "wq-v1-lite"

        by_time = await admin_client.get(
            "/admin/audit-logs",
            params={"start_at": _iso(before), "end_at": _iso(utcnow() + timedelta(minutes=1))},
        )
        assert by_time.status_code == 200
        assert by_time.json()["total"] >= 2

        by_user = await admin_client.get(
            "/admin/audit-logs", params={"admin_username": settings.ADMIN_INIT_USERNAME}
        )
        assert by_user.json()["total"] >= 2

        # 倒序：第一条必须是最新
        all_logs = (await admin_client.get("/admin/audit-logs", params={"size": 5})).json()
        ids = [item["id"] for item in all_logs["items"]]
        assert ids == sorted(ids, reverse=True)

        # 查不到的条件返回空
        nothing = await admin_client.get("/admin/audit-logs", params={"action": "no_such_action"})
        assert nothing.json()["total"] == 0
    finally:
        await _restore_default_engine()
