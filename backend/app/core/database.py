"""SQLAlchemy 2.0 异步引擎与会话工厂（aiomysql 驱动，连接 Docker 内的真实 MySQL 8.0）。

同时提供同步引擎（pymysql），供 `services/handoff_sync.py` 被同伴的 Celery Worker 直接调用。
两套引擎共用同一个 MySQL 实例与同一份表结构，逻辑必须完全一致（见 docs/handoff-contract.md §5）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    # 100 人并发考试场景：20 个常驻连接 + 30 个溢出，足够支撑并发抢配额压测
    pool_size=20,
    max_overflow=30,
    pool_recycle=1800,
    pool_timeout=30,
    future=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 同步引擎：Celery Worker 进程用（不依赖事件循环）
sync_engine = create_engine(
    settings.sync_database_url,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    pool_timeout=30,
    future=True,
)

SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个会话。"""
    async with AsyncSessionLocal() as session:
        yield session


@contextmanager
def sync_session() -> Iterator[Session]:
    """同步会话上下文（Celery Worker 侧使用）。"""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def check_mysql() -> bool:
    """连通性探测：SELECT 1。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - 探测失败一律返回 False
        logger.exception("MySQL 连通性检查失败")
        return False


async def dispose_engine() -> None:
    """关闭连接池（应用关闭 / 测试收尾调用）。"""
    await engine.dispose()


def dispose_sync_engine() -> None:
    """关闭同步连接池。"""
    sync_engine.dispose()
