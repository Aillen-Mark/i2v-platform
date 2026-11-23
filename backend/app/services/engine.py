"""默认引擎解析：优先 Redis `i2v:engine:default`，未命中则回源 `engines` 表并回填缓存。

Redis Hash 字段沿用 docs/handoff-contract.md §6 的约定：
    model / resolution / duration / max_concurrency / status / is_default
业务端只暴露对外代号 `code`，供应商与真实模型名不外泄。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.models import Engine

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_KEY = "i2v:engine:default"
ENGINE_CACHE_TTL = 600

_FALLBACK: dict[str, Any] = {
    "code": "wq-v1",
    "model_name": "MiniMax-H3-Highspeed",
    "resolution": "768P",
    "duration": 15,
    "max_concurrency": 25,
}


def _from_cache(raw: dict[str, str]) -> dict[str, Any] | None:
    code = (raw or {}).get("code")
    if not code:
        return None
    return {
        "code": code,
        "model_name": raw.get("model") or raw.get("model_name") or _FALLBACK["model_name"],
        "resolution": raw.get("resolution") or _FALLBACK["resolution"],
        "duration": int(raw.get("duration") or _FALLBACK["duration"]),
        "max_concurrency": int(raw.get("max_concurrency") or _FALLBACK["max_concurrency"]),
    }


def _from_orm(engine: Engine) -> dict[str, Any]:
    return {
        "code": engine.code,
        "model_name": engine.model_name,
        "resolution": engine.resolution,
        "duration": int(engine.duration or 15),
        "max_concurrency": int(engine.max_concurrency or 25),
    }


async def get_default_engine(session: AsyncSession) -> dict[str, Any]:
    """返回当前默认启用引擎（dict）；Redis 缓存失效时回源数据库并回填。"""
    try:
        cached = await redis_client.hgetall(DEFAULT_ENGINE_KEY)
        parsed = _from_cache(cached)
        if parsed:
            return parsed
    except Exception:  # noqa: BLE001 - Redis 抖动不应阻断提交，回源数据库即可
        logger.warning("读取默认引擎缓存失败，回源数据库", exc_info=True)

    engine = (
        await session.execute(
            select(Engine)
            .where(Engine.is_default == 1, Engine.status == 1)
            .order_by(Engine.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if engine is None:
        engine = (
            await session.execute(select(Engine).where(Engine.status == 1).order_by(Engine.id.asc()).limit(1))
        ).scalar_one_or_none()

    if engine is None:
        logger.warning("engines 表中没有启用引擎，使用内置兜底配置 %s", _FALLBACK["code"])
        return dict(_FALLBACK)

    payload = _from_orm(engine)
    try:
        await redis_client.hset(
            DEFAULT_ENGINE_KEY,
            mapping={
                "code": payload["code"],
                "model": payload["model_name"],
                "resolution": payload["resolution"],
                "duration": str(payload["duration"]),
                "max_concurrency": str(payload["max_concurrency"]),
                "status": "1",
                "is_default": "1",
            },
        )
        await redis_client.expire(DEFAULT_ENGINE_KEY, ENGINE_CACHE_TTL)
    except Exception:  # noqa: BLE001 - 缓存写失败不影响业务
        logger.warning("回填默认引擎缓存失败", exc_info=True)
    return payload


async def invalidate_engine_cache() -> None:
    """引擎配置变更后由管理端调用。"""
    await redis_client.delete(DEFAULT_ENGINE_KEY)
