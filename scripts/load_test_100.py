"""里程碑 D6：100 人并发压测（真实后端 + 真实 MySQL/Redis/MinIO）。

为什么用「多进程压测客户端」：
    单个 Python 进程内的 asyncio 事件循环在 Windows 上处理 100 条并发连接时，
    客户端自身就会成为瓶颈（实测同一台机器：单进程 100 并发 P95 ≈ 3.6s，
    4 进程 × 25 并发 P95 ≈ 0.7s）。真实考场是 100 个浏览器进程，
    因此用 4 个客户端进程各跑 25 并发，才是对服务端能力的忠实测量。

场景 A：100 考生并发登录 → 并发建草稿 → 并发提交生成
    断言：0 个 HTTP 500；提交接口 P95 ≤ 1.0s；quotas 扣减总数严格 = 100
场景 B：100 并发获取签名下载链接 → 100 并发 Range 拉取成片首块
    断言：100% 返回 206 Partial Content；首包（TTFB）P95 ≤ 1.0s

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\load_test_100.py
环境变量：
    LOAD_BASE=http://127.0.0.1:8080   压测目标（默认穿透 Nginx 网关）
    LOAD_COUNT=100                     并发人数
    LOAD_WORKERS=4                     客户端进程数
"""

from __future__ import annotations

import asyncio
import io
import multiprocessing
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pymysql
from openpyxl import Workbook

BASE = os.environ.get("LOAD_BASE", "http://127.0.0.1:8080").rstrip("/")
COUNT = int(os.environ.get("LOAD_COUNT", "100"))
WORKERS = int(os.environ.get("LOAD_WORKERS", "4"))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "Admin@2026!")

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)

TAG = uuid.uuid4().hex[:6].upper()
ACCOUNT_PREFIX = f"LOAD{TAG}"


# --------------------------------------------------------------------------- #
# 子进程：一个客户端进程负责 slice 内的并发
# --------------------------------------------------------------------------- #
async def _run_worker(payload: dict) -> dict:
    base = payload["base"]
    accounts: list[str] = payload["accounts"]
    creds: dict[str, str] = payload["credentials"]
    question_id: int = payload["question_id"]
    works: dict[str, int] = payload["works"]
    mode: str = payload["mode"]

    out: dict[str, list[float]] = {"login": [], "draft": [], "generate": [], "download": [], "range": []}
    statuses: dict[str, dict[int, int]] = {key: {} for key in out}
    errors: list[str] = []
    produced: dict[str, int] = {}

    def record(phase: str, elapsed: float, status: int) -> None:
        out[phase].append(elapsed)
        statuses[phase][status] = statuses[phase].get(status, 0) + 1

    clients: dict[str, httpx.AsyncClient] = {}

    async def login(account: str) -> None:
        client = httpx.AsyncClient(base_url=base, trust_env=False, timeout=180.0)
        started = time.perf_counter()
        try:
            response = await client.post("/api/login", json={"account": account, "password": creds[account]})
            record("login", time.perf_counter() - started, response.status_code)
            if response.status_code == 200:
                clients[account] = client
                return
            errors.append(f"login {account}: {response.status_code}")
        except Exception as exc:  # noqa: BLE001
            record("login", time.perf_counter() - started, 0)
            errors.append(f"login {account}: {type(exc).__name__}")
        await client.aclose()

    await asyncio.gather(*[login(account) for account in accounts])

    if mode == "submit":

        async def draft(account: str) -> None:
            client = clients[account]
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/api/works",
                    data={
                        "question_id": str(question_id),
                        "prompt": "压测：一只橘猫在雪地里奔跑，夕阳逆光",
                        "aspect_ratio": "16:9",
                    },
                    files={"image": ("frame.png", PNG_1X1, "image/png")},
                )
                record("draft", time.perf_counter() - started, response.status_code)
                if response.status_code == 200:
                    produced[account] = response.json()["id"]
                else:
                    errors.append(f"draft {account}: {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                record("draft", time.perf_counter() - started, 0)
                errors.append(f"draft {account}: {type(exc).__name__}")

        await asyncio.gather(*[draft(account) for account in clients])

        async def generate(account: str) -> None:
            client = clients[account]
            started = time.perf_counter()
            try:
                response = await client.post(f"/api/works/{produced[account]}/generate")
                record("generate", time.perf_counter() - started, response.status_code)
                if response.status_code != 200:
                    errors.append(f"generate {account}: {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                record("generate", time.perf_counter() - started, 0)
                errors.append(f"generate {account}: {type(exc).__name__}")

        await asyncio.gather(*[generate(account) for account in produced])
    else:
        raw = httpx.AsyncClient(
            trust_env=False,
            timeout=180.0,
            limits=httpx.Limits(max_connections=len(accounts) + 10, max_keepalive_connections=len(accounts) + 10),
        )

        async def fetch(account: str) -> None:
            work_id = works[account]
            if account not in clients:  # 该账号登录失败（已记入 errors），跳过下载
                return
            client = clients[account]
            started = time.perf_counter()
            try:
                response = await client.get(f"/api/works/{work_id}/download")
                record("download", time.perf_counter() - started, response.status_code)
                if response.status_code != 200:
                    errors.append(f"download {account}: {response.status_code}")
                    return
                url = response.json()["download_url"]
                started_range = time.perf_counter()
                async with raw.stream("GET", url, headers={"Range": "bytes=0-65535"}) as streamed:
                    status = streamed.status_code
                    async for chunk in streamed.aiter_bytes():
                        if chunk:
                            break
                record("range", time.perf_counter() - started_range, status)
                if status != 206:
                    errors.append(f"range {account}: {status}")
            except Exception as exc:  # noqa: BLE001
                record("download", time.perf_counter() - started, 0)
                errors.append(f"download {account}: {type(exc).__name__}")

        await asyncio.gather(*[fetch(account) for account in accounts])
        await raw.aclose()

    for client in clients.values():
        await client.aclose()

    return {"latencies": out, "statuses": statuses, "errors": errors, "works": produced}


def worker_entry(payload: dict) -> dict:
    return asyncio.run(_run_worker(payload))


# --------------------------------------------------------------------------- #
# 指标聚合
# --------------------------------------------------------------------------- #
def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize(name: str, values: list[float], statuses: dict[int, int]) -> str:
    if not values:
        return f"{name}: 无数据"
    return (
        f"{name}: n={len(values)} "
        f"P50={percentile(values, 50) * 1000:.0f}ms "
        f"P95={percentile(values, 95) * 1000:.0f}ms "
        f"Max={max(values) * 1000:.0f}ms "
        f"Avg={statistics.mean(values) * 1000:.0f}ms "
        f"状态码={dict(sorted(statuses.items()))}"
    )


def mysql_settings() -> dict:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return {
        "host": values.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(values.get("MYSQL_PORT", "13306")),
        "user": values.get("MYSQL_USER", "i2v"),
        "password": values.get("MYSQL_PASSWORD", "i2v_pwd_2026"),
        "database": values.get("MYSQL_DATABASE", "i2v_platform"),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def run_pool(payloads: list[dict]) -> list[dict]:
    """多进程跑压测客户端（Windows 用 spawn，入口函数必须在模块顶层）。"""
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=len(payloads), mp_context=context) as pool:
        return list(pool.map(worker_entry, payloads))


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def main() -> int:
    print(f"===== 100 人并发压测开始（目标 {BASE}，并发 {COUNT}，客户端进程 {WORKERS}）=====")
    failures: list[str] = []

    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=180.0) as admin:
        login_admin = await admin.post("/admin/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PWD})
        if login_admin.status_code != 200:
            print("管理员登录失败：", login_admin.text)
            return 1

        question = await admin.post(
            "/admin/questions",
            json={
                "code": f"L{TAG[:3]}",
                "title": f"压测题目 {TAG}",
                "description": "D6 百人并发压测用题目",
                "default_quota": 2,
                "sort_order": 999,
                "status": 1,
            },
        )
        if question.status_code != 200:
            print("建题目失败：", question.text)
            return 1
        question_id = question.json()["id"]

        now = time.time()
        window = await admin.post(
            "/admin/time-windows",
            json={
                "name": f"压测场次 {TAG}",
                "start_at": datetime.fromtimestamp(now - 300, timezone.utc).isoformat(),
                "end_at": datetime.fromtimestamp(now + 3 * 3600, timezone.utc).isoformat(),
                "status": 1,
            },
        )
        if window.status_code != 200:
            print("建时段失败：", window.text)
            return 1

        accounts = [f"{ACCOUNT_PREFIX}{index:03d}" for index in range(1, COUNT + 1)]
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["姓名", "账号", "备注"])
        for index, account in enumerate(accounts, start=1):
            sheet.append([f"压测考生{index:03d}", account, "D6 压测"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        imported = await admin.post(
            "/admin/candidates/import",
            files={
                "file": (
                    "load.xlsx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        if imported.status_code != 200 or imported.json()["success_count"] != COUNT:
            print("导入压测考生失败：", imported.text[:300])
            return 1

        listing = await admin.get("/admin/candidates", params={"keyword": ACCOUNT_PREFIX, "size": 200})
        ids = [item["id"] for item in listing.json()["items"]]
        slips = (await admin.get("/admin/candidates/password-slips", params={"ids": ",".join(map(str, ids))})).json()
        credentials = {item["account"]: item["password"] for item in slips if item["password"]}
        if len(credentials) != COUNT:
            print(f"取密码失败：拿到 {len(credentials)} / {COUNT}")
            return 1

    print(f"[准备] 已导入 {COUNT} 名压测考生（前缀 {ACCOUNT_PREFIX}），题目 id={question_id}，等待 3s 让库稳定")
    await asyncio.sleep(3)

    # 预热：把 Nginx 的上游 keepalive 连接池与 TCP 窗口撑开，
    # 否则 100 条并发「新建」连接会在 Windows 上丢 SYN，尾部请求要等 2~7 秒。
    async with httpx.AsyncClient(
        base_url=BASE,
        trust_env=False,
        timeout=60.0,
        limits=httpx.Limits(max_connections=COUNT + 40, max_keepalive_connections=COUNT + 40),
    ) as warm:
        await asyncio.gather(*[warm.get("/healthz") for _ in range(COUNT + 30)])
    print(f"[准备] 已完成 {COUNT + 30} 次网关预热")

    # ---------- 场景 A ----------
    chunk = (COUNT + WORKERS - 1) // WORKERS
    slices = [accounts[i : i + chunk] for i in range(0, COUNT, chunk)]
    submit_payloads = [
        {
            "base": BASE,
            "accounts": part,
            "credentials": credentials,
            "question_id": question_id,
            "works": {},
            "mode": "submit",
        }
        for part in slices
    ]
    started = time.perf_counter()
    submit_results = run_pool(submit_payloads)
    wall = time.perf_counter() - started

    lat: dict[str, list[float]] = {"login": [], "draft": [], "generate": []}
    statuses: dict[str, dict[int, int]] = {"login": {}, "draft": {}, "generate": {}}
    works: dict[str, int] = {}
    errors: list[str] = []
    for result in submit_results:
        for phase in lat:
            lat[phase].extend(result["latencies"].get(phase, []))
            for code, count in result["statuses"].get(phase, {}).items():
                statuses[phase][code] = statuses[phase].get(code, 0) + count
        works.update(result.get("works", {}))
        errors.extend(result.get("errors", []))

    print(f"[A1] {summarize('并发登录', lat['login'], statuses['login'])}")
    print(f"[A2] {summarize('并发建草稿', lat['draft'], statuses['draft'])}")
    print(f"[A3] {summarize('并发提交生成', lat['generate'], statuses['generate'])}")
    print(
        f"[A3] 提交成功率 {statuses['generate'].get(200, 0) / COUNT * 100:.1f}%，"
        f"P95 {percentile(lat['generate'], 95) * 1000:.0f}ms，整轮耗时 {wall:.1f}s"
    )
    if errors:
        print(f"[A ] 客户端错误 {len(errors)} 条，示例：{errors[:3]}")
    if statuses["generate"].get(200, 0) != COUNT:
        failures.append(f"提交成功数 {statuses['generate'].get(200, 0)} != {COUNT}")
    for phase in ("login", "draft", "generate"):
        if any(code == 0 or code >= 500 for code in statuses[phase]):
            failures.append(f"{phase} 出现连接失败或 5xx：{statuses[phase]}")
    if percentile(lat["generate"], 95) > 1.0:
        failures.append(f"提交 P95 {percentile(lat['generate'], 95) * 1000:.0f}ms > 1000ms")

    # ---------- 配额扣减核对 ----------
    settings = mysql_settings()
    connection = pymysql.connect(**settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(quota_used),0) FROM quotas WHERE question_id=%s", (question_id,))
            used_total = int(cursor.fetchone()[0])
    finally:
        connection.close()
    print(f"[A4] quotas 扣减总数={used_total}（期望 {COUNT}）")
    if used_total != COUNT:
        failures.append(f"配额扣减 {used_total} != {COUNT}（超发或漏扣）")

    # ---------- 等待出片 ----------
    print("[等待] Mock 出片（真实写入 MinIO）…")
    deadline = time.time() + 180
    status_map: dict[str, int] = {}
    while time.time() < deadline:
        connection = pymysql.connect(**settings)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT task_status, COUNT(*) FROM works WHERE question_id=%s GROUP BY task_status",
                    (question_id,),
                )
                status_map = dict(cursor.fetchall())
        finally:
            connection.close()
        if int(status_map.get("succeeded", 0)) + int(status_map.get("failed", 0)) >= COUNT:
            break
        await asyncio.sleep(2)
    print(f"[等待] 终态分布：{status_map}")

    connection = pymysql.connect(**settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, candidate_id FROM works WHERE question_id=%s AND task_status='succeeded'",
                (question_id,),
            )
            succeeded_rows = cursor.fetchall()
    finally:
        connection.close()

    if len(succeeded_works := succeeded_rows) < COUNT:
        failures.append(f"出片成功 {len(succeeded_works)} 条 < {COUNT}")

    # 把 work_id 映射回账号
    by_candidate = {}
    for work_id, candidate_id in succeeded_works:
        by_candidate[candidate_id] = work_id
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=60.0) as admin2:
        await admin2.post("/admin/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PWD})
        listing = await admin2.get("/admin/candidates", params={"keyword": ACCOUNT_PREFIX, "size": 200})
        id_to_account = {item["id"]: item["account"] for item in listing.json()["items"]}
    works_for_download = {
        id_to_account[candidate_id]: work_id
        for candidate_id, work_id in by_candidate.items()
        if candidate_id in id_to_account
    }

    # ---------- 场景 B ----------
    download_slices = [
        list(works_for_download.items())[i : i + chunk] for i in range(0, len(works_for_download), chunk)
    ]
    download_payloads = [
        {
            "base": BASE,
            "accounts": [account for account, _ in part],
            "credentials": credentials,
            "question_id": question_id,
            "works": dict(part),
            "mode": "download",
        }
        for part in download_slices
    ]
    started = time.perf_counter()
    download_results = run_pool(download_payloads)
    wall = time.perf_counter() - started

    lat_b: dict[str, list[float]] = {"download": [], "range": []}
    statuses_b: dict[str, dict[int, int]] = {"download": {}, "range": {}}
    errors_b: list[str] = []
    for result in download_results:
        for phase in lat_b:
            lat_b[phase].extend(result["latencies"].get(phase, []))
            for code, count in result["statuses"].get(phase, {}).items():
                statuses_b[phase][code] = statuses_b[phase].get(code, 0) + count
        errors_b.extend(result.get("errors", []))

    print(f"[B1] {summarize('并发获取签名下载链接', lat_b['download'], statuses_b['download'])}")
    print(f"[B2] {summarize('并发 Range 拉取首块(TTFB)', lat_b['range'], statuses_b['range'])}")
    print(
        f"[B2] 206 成功率 {statuses_b['range'].get(206, 0) / max(len(works_for_download), 1) * 100:.1f}%，"
        f"首包 P95 {percentile(lat_b['range'], 95) * 1000:.0f}ms，整轮耗时 {wall:.1f}s"
    )
    if errors_b:
        print(f"[B ] 客户端错误 {len(errors_b)} 条，示例：{errors_b[:3]}")
    if statuses_b["range"].get(206, 0) != len(works_for_download):
        failures.append(f"206 成功 {statuses_b['range'].get(206, 0)} != {len(works_for_download)}")
    if percentile(lat_b["range"], 95) > 1.0:
        failures.append(f"Range 首包 P95 {percentile(lat_b['range'], 95) * 1000:.0f}ms > 1000ms")
    if any(code == 0 or code >= 500 for code in statuses_b["download"]):
        failures.append(f"下载链接接口异常：{statuses_b['download']}")

    # ---------- 清理 ----------
    connection = pymysql.connect(**settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM works WHERE question_id=%s", (question_id,))
            cursor.execute("DELETE FROM quotas WHERE question_id=%s", (question_id,))
            cursor.execute("DELETE FROM candidates WHERE account LIKE %s", (f"{ACCOUNT_PREFIX}%",))
            cursor.execute("DELETE FROM questions WHERE id=%s", (question_id,))
            cursor.execute("DELETE FROM time_windows WHERE name LIKE %s", (f"压测场次 {TAG}%",))
        connection.commit()
    finally:
        connection.close()
    print("[清理] 已删除压测批次（考生、作品、配额、题目、时段）")

    print("===== 压测结论 =====")
    if failures:
        for item in failures:
            print(" [FAIL]", item)
        return 1
    print(" 全部通过 ✅ 0 个 5xx；提交 P95 与下载首包 P95 均 ≤ 1.0s；配额扣减严格 = 100")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(asyncio.run(main()))
