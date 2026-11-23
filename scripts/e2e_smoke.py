"""端到端冒烟验证（M5）：全部经 Vite Dev Server 代理访问后端，验证浏览器同源链路。

链路：/healthz → 管理员登录 → 建考生/时段 → 考生登录（cand_sid Cookie）→ 题目列表
      → 上传首帧图创建草稿 → 提交生成 → 轮询出片 → 在线预览（Range 206）→ 签名下载
      → 打包下载 zip → 校验 zip 内文件

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\e2e_smoke.py
前置：
    uvicorn（127.0.0.1:8000）+ npm run dev（127.0.0.1:5173）均已启动
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time
import uuid
import zipfile

import httpx

PROXY = os.environ.get("SMOKE_BASE", "http://127.0.0.1:5173")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "Admin@2026!")

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)

FAILURES: list[str] = []


def ok(step: str, extra: str = "") -> None:
    print(f"[OK] {step}{(' -> ' + extra) if extra else ''}")


def fail(step: str, detail: str) -> None:
    print(f"[FAIL] {step}: {detail}")
    FAILURES.append(f"{step}: {detail}")


async def main() -> int:
    tag = uuid.uuid4().hex[:6].upper()
    account = f"E2E{tag}"
    print(f"== 端到端冒烟开始（经 {PROXY} 代理）==")

    async with httpx.AsyncClient(base_url=PROXY, trust_env=False, timeout=120.0) as client:
        # 1) 健康检查
        health = await client.get("/healthz")
        if health.status_code == 200 and health.json().get("mysql") and health.json().get("redis"):
            ok("GET /healthz（经代理）", health.text.strip())
        else:
            fail("GET /healthz", health.text)
            return 1

        # 2) 管理员登录
        login_admin = await client.post(
            "/admin/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PWD}
        )
        if login_admin.status_code != 200:
            fail("管理员登录", login_admin.text)
            return 1
        ok("POST /admin/auth/login", "admin_sid 已下发")

        # 3) 建考生
        created = await client.post(
            "/admin/candidates", json={"name": f"冒烟{tag}", "account": account}
        )
        if created.status_code != 200:
            fail("新增考生", created.text)
            return 1
        password = created.json()["password"]
        ok("POST /admin/candidates", f"{account} / 初始密码 {password}")

        # 4) 建题目
        question = await client.post(
            "/admin/questions",
            json={
                "code": f"E{tag[:3]}",
                "title": "冒烟题目",
                "description": "端到端冒烟用题目",
                "default_quota": 2,
                "sort_order": 1,
                "status": 1,
            },
        )
        if question.status_code != 200:
            fail("新增题目", question.text)
            return 1
        question_id = question.json()["id"]
        ok("POST /admin/questions", f"id={question_id}")

        # 5) 开一个覆盖当前时间的时段
        now = time.time()
        from datetime import datetime, timedelta, timezone

        start = datetime.fromtimestamp(now - 300, timezone.utc).isoformat()
        end = datetime.fromtimestamp(now + 3600, timezone.utc).isoformat()
        window = await client.post(
            "/admin/time-windows",
            json={"name": f"冒烟场次{tag}", "start_at": start, "end_at": end, "status": 1},
        )
        if window.status_code != 200:
            fail("新增时段", window.text)
            return 1
        ok("POST /admin/time-windows", "已开放当前时段")

        # 6) 考生登录（独立 Cookie Jar）
        async with httpx.AsyncClient(base_url=PROXY, trust_env=False, timeout=120.0) as cand:
            login = await cand.post("/api/login", json={"account": account, "password": password})
            if login.status_code != 200:
                fail("考生登录", login.text)
                return 1
            if "cand_sid" not in cand.cookies:
                fail("考生登录", "未拿到 cand_sid Cookie")
                return 1
            ok("POST /api/login", f"cand_sid 已下发，总配额 {login.json()['total_quota']}")

            me = (await cand.get("/api/me")).json()
            if not me["in_window"]:
                fail("GET /api/me", f"时段未生效：{me}")
                return 1
            ok("GET /api/me", f"in_window=true，剩余 {me['total_remaining']} 次")

            questions = (await cand.get("/api/questions")).json()
            target = next((q for q in questions if q["id"] == question_id), None)
            if not target:
                fail("GET /api/questions", "新建题目未出现在考生端")
                return 1
            ok("GET /api/questions", f"共 {len(questions)} 题，剩余 {target['remaining']} 次")

            # 7) 上传首帧图创建草稿
            draft = await cand.post(
                "/api/works",
                data={"question_id": str(question_id), "prompt": "一只橘猫在雪地里奔跑", "aspect_ratio": "16:9"},
                files={"image": ("frame.png", PNG_1X1, "image/png")},
            )
            if draft.status_code != 200:
                fail("POST /api/works", draft.text)
                return 1
            work = draft.json()
            ok("POST /api/works", f"task_no={work['task_no']}，image_url 可访问={bool(work['image_url'])}")

            # 8) 提交生成（Mock 默认 80% 成功，失败会退还次数，允许自动重试一次）
            detail = None
            for attempt in range(1, 4):
                submitted = await cand.post(f"/api/works/{work['id']}/generate")
                if submitted.status_code != 200:
                    fail("POST /generate", submitted.text)
                    return 1
                ok(
                    "POST /api/works/{id}/generate",
                    f"第 {attempt} 次提交：status={submitted.json()['task_status']}，seq={submitted.json()['seq']}",
                )

                # 9) 轮询出片
                deadline = time.time() + 60
                while time.time() < deadline:
                    detail = (await cand.get(f"/api/works/{work['id']}")).json()
                    if detail["task_status"] in ("succeeded", "failed"):
                        break
                    await asyncio.sleep(1.5)

                if detail and detail["task_status"] == "succeeded":
                    break

                # 失败：Mock 会退还次数，重新建草稿再试
                print(f"      Mock 本次随机失败（{detail and detail.get('error_msg')}），已退还次数，重试…")
                redraft = await cand.post(
                    "/api/works",
                    data={"question_id": str(question_id), "prompt": "一只橘猫在雪地里奔跑", "aspect_ratio": "16:9"},
                    files={"image": ("frame.png", PNG_1X1, "image/png")},
                )
                if redraft.status_code != 200:
                    fail("重试建草稿", redraft.text)
                    return 1
                work = redraft.json()

            if not detail or detail["task_status"] != "succeeded":
                fail("轮询出片", f"最终状态 {detail and detail['task_status']}")
                return 1
            ok("GET /api/works/{id}", f"succeeded，video_url 存在={bool(detail['video_url'])}")

            # 10) 在线预览（Range 206）
            async with httpx.AsyncClient(trust_env=False, timeout=60.0) as raw:
                ranged = await raw.get(detail["video_url"], headers={"Range": "bytes=0-1023"})
            if ranged.status_code == 206 and len(ranged.content) == 1024:
                ok("预览 URL + Range", f"206 / {len(ranged.content)} 字节")
            else:
                fail("预览 URL + Range", f"{ranged.status_code} / {len(ranged.content)}")

            # 11) 作品列表 + 签名下载
            works = (await cand.get("/api/me/works")).json()
            if works["total"] < 1 or not works["items"][0]["preview_url"]:
                fail("GET /api/me/works", str(works)[:200])
            else:
                ok("GET /api/me/works", f"共 {works['total']} 条，preview_url 就绪")

            download = await cand.get(f"/api/works/{work['id']}/download")
            if download.status_code != 200:
                fail("GET /download", download.text)
            else:
                body = download.json()
                ok("GET /api/works/{id}/download", f"{body['filename']}（{body['expires_in']}s）")

            # 12) 打包下载
            zipped = await cand.get("/api/me/download-all")
            if zipped.status_code != 200:
                fail("GET /api/me/download-all", zipped.text)
            else:
                archive = zipfile.ZipFile(io.BytesIO(zipped.content))
                names = archive.namelist()
                if len(names) == 1 and names[0].endswith(".mp4"):
                    ok("GET /api/me/download-all", f"zip 内 {names[0]}")
                else:
                    fail("GET /api/me/download-all", str(names))

        # 13) 清理：删掉冒烟考生与题目（保留作品便于人工查看时可不删）
        await client.delete(f"/admin/candidates/{created.json()['id']}")
        ok("DELETE /admin/candidates/{id}", "冒烟考生已清理")

    print("== 冒烟结束 ==")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(" -", item)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
