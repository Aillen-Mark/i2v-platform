"""FastAPI 应用入口：Lifespan 自检（MySQL / Redis / MinIO 桶 / 默认管理员）+ 路由注册 + 统一错误格式。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.admin import auth as admin_auth
from app.admin import audit_logs as admin_audit_logs
from app.admin import candidates as admin_candidates
from app.admin import engines as admin_engines
from app.admin import questions as admin_questions
from app.admin import quotas as admin_quotas
from app.admin import stats as admin_stats
from app.admin import time_windows as admin_time_windows
from app.api import auth as api_auth
from app.api import questions as api_questions
from app.api import works as api_works
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, check_mysql, dispose_engine, dispose_sync_engine
from app.core.errors import register_exception_handlers
from app.core.redis import check_redis, close_redis
from app.core.security import hash_password
from app.core.storage import ensure_buckets
from app.models import Admin
from app.services.hashing import shutdown_pool as shutdown_hash_pool
from app.services.hashing import warmup as warmup_hash_pool

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


async def _wait_service(
    checker: Callable[[], Awaitable[bool]],
    name: str,
    attempts: int = 10,
    delay: float = 2.0,
) -> bool:
    """容器刚起来时可能有几秒不可用，做有限次重试。"""
    for attempt in range(1, attempts + 1):
        if await checker():
            logger.info("%s 连通性检查通过（第 %s 次尝试）", name, attempt)
            return True
        logger.warning("%s 尚未就绪（第 %s/%s 次尝试）", name, attempt, attempts)
        if attempt < attempts:
            await asyncio.sleep(delay)
    return False


async def ensure_default_admin() -> None:
    """admins 表为空时，用 .env 的账号密码创建默认管理员。

    多 worker（`--workers 4`）会同时启动，这里用「先查后插 + 捕获唯一键冲突」保证幂等：
    抢输的 worker 只会拿到 IntegrityError，按 info 级别记录即可，不能影响启动。
    """
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(func.count()).select_from(Admin))
        if existing:
            logger.info("admins 表已有 %s 条记录，跳过默认管理员初始化", existing)
            return
        session.add(
            Admin(
                username=settings.ADMIN_INIT_USERNAME,
                password_hash=hash_password(settings.admin_password),
                role="admin",
                status=1,
            )
        )
        try:
            await session.commit()
            logger.info("已创建默认管理员：%s", settings.ADMIN_INIT_USERNAME)
        except IntegrityError:
            # 并发启动：另一个 worker 抢先创建成功，属于预期情况
            await session.rollback()
            logger.info("默认管理员已由其他 worker 创建，跳过")
        except Exception:  # noqa: BLE001 - 其它异常只记录，不阻断启动
            await session.rollback()
            logger.exception("创建默认管理员失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动：%s (%s)", settings.APP_NAME, settings.APP_ENV)

    # bcrypt 校验走 asyncio.to_thread 的默认线程池（默认 min(32, cpu+4)=20 线程）。
    # 100 并发登录时每个 worker 要同时挂 ~25 个 bcrypt，20 线程会成为硬上限，
    # 这里放大到 32，让 CPU 密集的 bcrypt 充分并行（bcrypt 释放 GIL）。
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32, thread_name_prefix="i2v-worker"))

    if not await _wait_service(check_mysql, "MySQL"):
        raise RuntimeError(
            "MySQL 连接失败：请确认 Docker Desktop 已启动、"
            f"docker compose ps 中 mysql 为 healthy，且 .env 端口为 {settings.MYSQL_PORT}"
        )
    if not await _wait_service(check_redis, "Redis"):
        raise RuntimeError(
            "Redis 连接失败：请确认 Docker Desktop 已启动、"
            f"docker compose ps 中 redis 为 healthy，且 .env 端口为 {settings.REDIS_PORT}"
        )

    buckets = await asyncio.to_thread(ensure_buckets)
    logger.info("对象存储桶就绪：%s", buckets)

    await ensure_default_admin()

    # 预热批量哈希进程池：让 16 个 spawn 子进程提前就位，
    # 否则首次 Excel 导入要在请求里额外付 1~2 秒的进程启动开销。
    if await warmup_hash_pool():
        logger.info("哈希进程池预热完成")
    else:
        logger.warning("哈希进程池预热失败，导入/重置密码将降级为线程池")

    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        await close_redis()
        await dispose_engine()
        await asyncio.to_thread(dispose_sync_engine)
        await asyncio.to_thread(shutdown_hash_pool)
        logger.info("应用已关闭：连接池、Redis 客户端与哈希进程池已释放")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.2.0",
    description="图生视频创作平台后端（M2：双端隔离认证 / Excel 千行导入 / 题目与配额联动）",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.include_router(api_auth.router)
app.include_router(api_questions.router)
app.include_router(api_works.router)
app.include_router(admin_auth.router)
app.include_router(admin_candidates.router)
app.include_router(admin_questions.router)
app.include_router(admin_time_windows.router)
app.include_router(admin_quotas.router)
app.include_router(admin_engines.router)
app.include_router(admin_stats.router)
app.include_router(admin_audit_logs.router)


@app.get("/healthz", tags=["system"], summary="健康检查")
async def healthz(response: Response) -> dict[str, Any]:
    mysql_ok = await check_mysql()
    redis_ok = await check_redis()
    healthy = mysql_ok and redis_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "mysql": mysql_ok, "redis": redis_ok}


@app.get("/", tags=["system"], summary="根路径")
async def root() -> dict[str, str]:
    return {"app": settings.APP_NAME, "env": settings.APP_ENV, "docs": "/docs", "health": "/healthz"}
