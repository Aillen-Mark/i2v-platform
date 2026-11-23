"""pytest 全局夹具：真实 Docker MySQL / Redis / MinIO，不做任何数据库 Mock。

**测试环境物理隔离（M6 红线）**
- 本文件在被测代码之前执行，先把环境变量改写成：
    MYSQL_DATABASE = i2v_platform_test（独立测试库，session 启动时自动建库 + 同步 schema.sql）
    REDIS_DB       = 15（独立 Redis 库）
- 因此 `TRUNCATE` / `FLUSHDB` 只作用于测试库与 db=15，
  主库 `i2v_platform` 与 db=0 里的演示 / 真实数据永远不会被 pytest 清掉。

事件循环固定为 session 级（见 backend/pytest.ini）：AsyncEngine 的连接池与 aiomysql 连接
绑定创建它的事件循环，若每个测试用独立 loop，收尾时会报 "Event loop is closed"。
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# 1) 在任何 app 模块被导入之前切换数据库与 Redis 库
# --------------------------------------------------------------------------- #
os.environ["MYSQL_DATABASE"] = "i2v_platform_test"
os.environ["REDIS_DB"] = "15"
os.environ.setdefault("LOG_LEVEL", "WARNING")

import pymysql  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.redis import redis_client  # noqa: E402
from app.main import app  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT_DIR / "docs" / "schema.sql"
TEST_DATABASE = os.environ["MYSQL_DATABASE"]

# 会被用例改写的表；admins（默认管理员）与 engines（预置引擎）保持不动
MUTABLE_TABLES: tuple[str, ...] = (
    "works",
    "quotas",
    "candidates",
    "questions",
    "time_windows",
    "audit_logs",
)


def _schema_statements() -> list[str]:
    """把 docs/schema.sql 拆成可逐条执行的语句，并剔除切库语句（测试库已选定）。"""
    raw = SCHEMA_FILE.read_text(encoding="utf-8")
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(("CREATE DATABASE", "USE ")):
            continue
        kept.append(line)
    sql = "\n".join(kept)
    return [chunk.strip() for chunk in sql.split(";") if chunk.strip()]


def _bootstrap_test_database() -> None:
    """建测试库（若不存在）+ 给业务账号授权 + 同步表结构；幂等，可重复执行。

    用 root 连接完成建库与授权（MySQL 镜像只为 MYSQL_DATABASE 授权了业务账号），
    表结构用业务账号连接执行，确保业务账号对测试库有正常读写权限。
    """
    settings = get_settings()
    root = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user="root",
        password=settings.MYSQL_ROOT_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with root.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DATABASE}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(
                f"GRANT ALL PRIVILEGES ON `{TEST_DATABASE}`.* TO %s@'%%'",
                (settings.MYSQL_USER,),
            )
            cursor.execute("FLUSH PRIVILEGES")
    finally:
        root.close()

    conn = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=TEST_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            for statement in _schema_statements():
                # 表已存在（1050）/ 预置数据已存在（1062）都直接跳过，保证重复执行幂等
                try:
                    cursor.execute(statement)
                except pymysql.err.MySQLError as exc:
                    if exc.args and exc.args[0] in (1050, 1062):
                        continue
                    raise
    finally:
        conn.close()


_bootstrap_test_database()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app_client():
    """触发 FastAPI lifespan（MySQL/Redis 自检、建桶、初始化管理员），并返回 ASGI 客户端。"""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000", timeout=120.0) as client:
            yield client


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean_state(app_client):
    """每个测试前清空**测试库**业务表 + 清空 **Redis db=15**（主库与 db=0 不受影响）。

    先等上一轮的 Mock 协程收尾：TRUNCATE 会重置自增 ID，残留的 Mock 任务
    可能把新测试里复用了同一 ID 的作品误标为 running/succeeded。
    """
    from app.services import handoff as handoff_module

    remaining = await handoff_module.wait_mock_tasks(timeout=20.0)
    if remaining:
        print(f"[conftest] 仍有 {remaining} 个 Mock 协程未结束")

    async with AsyncSessionLocal() as session:
        for table in MUTABLE_TABLES:
            await session.execute(text(f"TRUNCATE TABLE `{table}`"))
        await session.commit()
    await redis_client.flushdb()
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def admin_client(clean_state):
    """已登录的管理端客户端（独立 Cookie Jar）。"""
    settings = get_settings()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000", timeout=120.0) as client:
        response = await client.post(
            "/admin/auth/login",
            json={"username": settings.ADMIN_INIT_USERNAME, "password": settings.admin_password},
        )
        assert response.status_code == 200, response.text
        yield client


@pytest_asyncio.fixture(loop_scope="session")
async def candidate_client_factory(clean_state):
    """按需创建独立的考生端客户端（每个客户端独立 Cookie Jar）。"""
    created: list[AsyncClient] = []

    def _new() -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            timeout=120.0,
        )
        created.append(client)
        return client

    yield _new

    for client in created:
        await client.aclose()
