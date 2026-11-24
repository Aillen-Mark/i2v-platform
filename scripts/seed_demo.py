"""演示数据播种（单题极简模式）。

做的事情：
1. 清理历史压测残留（LOAD% 考生、L% 压测题、压测场次时段）；
2. 保证库里**只有 1 道启用题目**：`Q1 / 图生视频创作`（其余题目一律停用，不删数据）；
3. 建（或重置）演示考生 `DEMO0001`，该题默认 2 次机会；
4. 保证有一个覆盖当前时间的开放时段。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pymysql

BASE = "http://127.0.0.1:8000"
ADMIN_USER = "admin"
ADMIN_PWD = "Admin@2026!"
DEMO_ACCOUNT = "DEMO0001"

QUESTION = {
    "code": "Q1",
    "title": "图生视频创作",
    "description": "请上传首帧图片并输入提示词生成 15 秒视频作品",
    "prompt_examples": [
        "镜头缓慢推近，主体清晰，电影质感，暖色调",
        "环绕运镜展示主体全貌，浅景深，自然光",
        "低角度仰拍，主体动态舒展，背景虚化",
    ],
    "default_quota": 2,
    "sort_order": 1,
    "status": 1,
}


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
        "autocommit": True,
    }


def cleanup_load_test_data() -> None:
    """清掉历次压测留下的考生/作品/题目/时段（不影响演示数据）。"""
    connection = pymysql.connect(**mysql_settings())
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM works WHERE candidate_id IN (SELECT id FROM candidates WHERE account LIKE 'LOAD%')")
            cursor.execute("DELETE FROM quotas WHERE candidate_id IN (SELECT id FROM candidates WHERE account LIKE 'LOAD%')")
            cursor.execute("DELETE FROM candidates WHERE account LIKE 'LOAD%'")
            cursor.execute("DELETE FROM works WHERE question_id IN (SELECT id FROM questions WHERE code LIKE 'L%')")
            cursor.execute("DELETE FROM quotas WHERE question_id IN (SELECT id FROM questions WHERE code LIKE 'L%')")
            cursor.execute("DELETE FROM questions WHERE code LIKE 'L%'")
            cursor.execute("DELETE FROM time_windows WHERE name LIKE '压测场次%'")
            cursor.execute("SELECT COUNT(*) FROM candidates")
            left = cursor.fetchone()[0]
    finally:
        connection.close()
    print(f"[清理] 压测残留已清理，剩余考生 {left} 名")


async def main() -> int:
    cleanup_load_test_data()

    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=60.0) as client:
        health = await client.get("/healthz")
        if health.status_code != 200:
            print("后端未就绪，请先启动 uvicorn：", health.text)
            return 1

        login = await client.post("/admin/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PWD})
        if login.status_code != 200:
            print("管理员登录失败：", login.text)
            return 1

        # ---- 1) 保证只有 1 道启用题目 ----
        questions = (await client.get("/admin/questions")).json()
        target = next((item for item in questions if item["code"] == QUESTION["code"]), None)
        if target is None:
            created = await client.post("/admin/questions", json=QUESTION)
            if created.status_code != 200:
                print("创建默认题目失败：", created.text)
                return 1
            target = created.json()
            print(f"[题目] 已创建 {target['code']} · {target['title']}")
        else:
            updated = await client.put(
                f"/admin/questions/{target['id']}",
                json={
                    "title": QUESTION["title"],
                    "description": QUESTION["description"],
                    "prompt_examples": QUESTION["prompt_examples"],
                    "default_quota": QUESTION["default_quota"],
                    "sort_order": QUESTION["sort_order"],
                    "status": 1,
                },
            )
            if updated.status_code != 200:
                print("更新默认题目失败：", updated.text)
                return 1
            target = updated.json()
            print(f"[题目] 已收敛为 {target['code']} · {target['title']}")

        disabled = []
        for item in questions:
            if item["id"] != target["id"] and item["status"] == 1:
                await client.put(f"/admin/questions/{item['id']}", json={"status": 0})
                disabled.append(item["code"])
        if disabled:
            print(f"[题目] 已停用多余题目：{', '.join(disabled)}")

        enabled = [item for item in (await client.get("/admin/questions")).json() if item["status"] == 1]
        print(f"[题目] 当前启用题目数 = {len(enabled)}（{enabled[0]['code']}）")

        # ---- 2) 演示考生 ----
        candidate = (await client.get("/admin/candidates", params={"keyword": DEMO_ACCOUNT})).json()
        password = None
        if candidate["total"]:
            candidate_id = candidate["items"][0]["id"]
            reset = await client.post(f"/admin/candidates/{candidate_id}/reset-password")
            password = reset.json()["password"]
            print(f"[考生] {DEMO_ACCOUNT} 已存在（id={candidate_id}），已重置密码")
        else:
            created = await client.post(
                "/admin/candidates", json={"name": "演示考生", "account": DEMO_ACCOUNT, "remark": "前端演示账号"}
            )
            if created.status_code != 200:
                print("创建演示考生失败：", created.text)
                return 1
            password = created.json()["password"]
            candidate_id = created.json()["id"]
            print(f"[考生] {DEMO_ACCOUNT} 已创建（id={candidate_id}）")

        # 演示考生的该题次数严格为 2
        quota = await client.put(
            f"/admin/quotas/by-candidate/{candidate_id}",
            json={"items": [{"question_id": target["id"], "quota_total": 2}], "force_confirm": True, "remark": "演示数据"},
        )
        print(f"[配额] {DEMO_ACCOUNT} 该题次数 = {2}（接口 {quota.status_code}）")

        # ---- 3) 开放时段 ----
        now = time.time()
        windows = (await client.get("/admin/time-windows")).json()
        if not windows.get("active_window_id"):
            start = datetime.fromtimestamp(now - 300, timezone.utc).isoformat()
            end = datetime.fromtimestamp(now + 4 * 3600, timezone.utc).isoformat()
            window = await client.post(
                "/admin/time-windows",
                json={"name": "演示场次（4 小时）", "start_at": start, "end_at": end, "status": 1},
            )
            print("[时段] 已创建覆盖当前时间的演示时段 ->", window.status_code)
        else:
            print("[时段] 已有生效时段，跳过创建")

        # ---- 4) 自检：考生端只应拿到 1 道题、总次数 2 ----
        async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=60.0) as cand:
            login_c = await cand.post("/api/login", json={"account": DEMO_ACCOUNT, "password": password})
            if login_c.status_code == 200:
                me = (await cand.get("/api/me")).json()
                qs = (await cand.get("/api/questions")).json()
                print(f"[自检] 考生端题目数={len(qs)}，总次数={me['total_quota']}，剩余={me['total_remaining']}，in_window={me['in_window']}")

    print("\n===== 浏览器体验入口 =====")
    print("前端： http://127.0.0.1:8080")
    print(f"考号： {DEMO_ACCOUNT}")
    print(f"密码： {password}")
    print("管理员： http://127.0.0.1:8080/console/login （admin / Admin@2026!）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
