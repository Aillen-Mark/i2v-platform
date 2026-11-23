"""演示数据播种：建 2 道题目 + 1 个演示考生 + 当前生效的开放时段。

用途：前端手动体验前跑一次，浏览器里直接用打印出的账号密码登录。
用法：backend\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8000"
ADMIN_USER = "admin"
ADMIN_PWD = "Admin@2026!"
DEMO_ACCOUNT = "DEMO0001"

QUESTIONS = [
    {
        "code": "Q1",
        "title": "雪地里的橘猫",
        "description": "以你上传的图片为首帧，生成 15 秒视频：画面中的主体在雪地中奔跑，镜头缓慢推近，夕阳逆光，电影质感。",
        "prompt_examples": [
            "一只橘猫在雪地里奔跑，夕阳逆光，镜头缓慢推近，电影质感",
            "橘猫跃起扑向雪花，慢动作，浅景深，暖色调",
            "橘猫坐在雪堆上回望镜头，呼吸带出白雾，柔光",
        ],
        "default_quota": 2,
        "sort_order": 1,
    },
    {
        "code": "Q2",
        "title": "城市夜景延时",
        "description": "以你上传的图片为首帧，生成 15 秒视频：城市夜景车流形成光轨，镜头缓慢横移，赛博朋克霓虹色调。",
        "prompt_examples": [
            "城市夜景车流光轨，镜头缓慢横移，赛博朋克霓虹",
            "雨后街道倒映霓虹灯，行人撑伞走过，浅景深",
            "高楼天台俯瞰城市，云层快速流动，延时摄影感",
        ],
        "default_quota": 2,
        "sort_order": 2,
    },
]


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=60.0) as client:
        health = await client.get("/healthz")
        if health.status_code != 200:
            print("后端未就绪，请先启动 uvicorn：", health.text)
            return 1

        login = await client.post("/admin/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PWD})
        if login.status_code != 200:
            print("管理员登录失败：", login.text)
            return 1

        existing = (await client.get("/admin/questions")).json()
        codes = {item["code"] for item in existing}
        for question in QUESTIONS:
            if question["code"] in codes:
                print(f"题目 {question['code']} 已存在，跳过")
                continue
            created = await client.post("/admin/questions", json={**question, "status": 1})
            print(f"题目 {question['code']} -> {created.status_code}")

        candidate = (await client.get("/admin/candidates", params={"keyword": DEMO_ACCOUNT})).json()
        password = None
        if candidate["total"]:
            candidate_id = candidate["items"][0]["id"]
            print(f"演示考生 {DEMO_ACCOUNT} 已存在（id={candidate_id}），重置密码…")
            reset = await client.post(f"/admin/candidates/{candidate_id}/reset-password")
            password = reset.json()["password"]
        else:
            created = await client.post(
                "/admin/candidates", json={"name": "演示考生", "account": DEMO_ACCOUNT, "remark": "前端演示账号"}
            )
            if created.status_code != 200:
                print("创建演示考生失败：", created.text)
                return 1
            password = created.json()["password"]
            candidate_id = created.json()["id"]
            print(f"演示考生 {DEMO_ACCOUNT} 已创建（id={candidate_id}）")

        now = time.time()
        windows = (await client.get("/admin/time-windows")).json()
        active = windows.get("active_window_id")
        if not active:
            start = datetime.fromtimestamp(now - 300, timezone.utc).isoformat()
            end = datetime.fromtimestamp(now + 4 * 3600, timezone.utc).isoformat()
            window = await client.post(
                "/admin/time-windows",
                json={"name": "演示场次（4 小时）", "start_at": start, "end_at": end, "status": 1},
            )
            print("开放时段 ->", window.status_code, window.json().get("name"))
        else:
            print("已有生效时段，跳过创建")

    print("\n===== 浏览器体验入口 =====")
    print("前端： http://127.0.0.1:5173")
    print(f"考号： {DEMO_ACCOUNT}")
    print(f"密码： {password}")
    print("管理员： http://127.0.0.1:8000/docs （admin / Admin@2026!）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
