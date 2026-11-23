"""redis.asyncio 客户端（decode_responses=True，连接 Docker 内的真实 Redis 7）。"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    health_check_interval=30,
    socket_connect_timeout=5,
    socket_keepalive=True,
)


async def check_redis() -> bool:
    """连通性探测：PING。"""
    try:
        return bool(await redis_client.ping())
    except Exception:  # noqa: BLE001 - 探测失败一律返回 False
        logger.exception("Redis 连通性检查失败")
        return False


async def close_redis() -> None:
    await redis_client.aclose()
