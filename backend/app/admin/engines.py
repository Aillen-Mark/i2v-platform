"""管理端模型引擎配置：列表（含健康度）、编辑、一键切换默认引擎（480P 降级）。

联动红线：任何写操作都必须同步
1) MySQL `engines` 表；
2) Redis `i2v:engine:config:{code}` 哈希（供生成模块读取）；
3) `invalidate_engine_cache()` 清掉 `i2v:engine:default`，保证考生下一次提交立即用新配置。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.core.redis import redis_client
from app.core.timeutil import utcnow
from app.models import Admin, Engine, Work
from app.schemas.engine import EngineHealthOut, EngineOut, EngineUpdateIn
from app.services.audit import write_audit
from app.services.engine import DEFAULT_ENGINE_KEY, invalidate_engine_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/engines", tags=["管理端 · 模型引擎"])

CONFIG_KEY = "i2v:engine:config:{code}"
HEALTH_KEY = "i2v:engine:health:{code}"
CONFIG_TTL = 86400
HEALTH_WINDOW_HOURS = 1
FAIL_RATE_ALERT = 0.2


def _config_payload(engine: Engine) -> dict[str, str]:
    return {
        "model": engine.model_name,
        "resolution": engine.resolution,
        "duration": str(int(engine.duration or 15)),
        "max_concurrency": str(int(engine.max_concurrency or 25)),
        "status": str(int(engine.status)),
        "is_default": str(int(engine.is_default)),
    }


async def sync_engine_config(engine: Engine) -> None:
    """把单条引擎配置写进 Redis 哈希（生成模块按 code 读取）。"""
    key = CONFIG_KEY.format(code=engine.code)
    await redis_client.hset(key, mapping=_config_payload(engine))
    await redis_client.expire(key, CONFIG_TTL)


async def _health_from_redis(code: str) -> EngineHealthOut | None:
    raw = await redis_client.hgetall(HEALTH_KEY.format(code=code))
    if not raw:
        return None
    return EngineHealthOut(
        recent_success=int(raw.get("recent_success") or 0),
        recent_fail=int(raw.get("recent_fail") or raw.get("recent_failed") or 0),
        avg_cost_sec=float(raw["avg_cost_sec"]) if raw.get("avg_cost_sec") else None,
        circuit_open=str(raw.get("circuit_open") or "0") in {"1", "true", "True"},
        source="redis",
    )


async def _health_from_db(session: AsyncSession, code: str) -> EngineHealthOut:
    """Redis 无数据时，用 works 最近 1 小时兜底统计。"""
    since = utcnow() - timedelta(hours=HEALTH_WINDOW_HOURS)
    success = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Work)
                .where(
                    Work.engine_code == code,
                    Work.task_status == "succeeded",
                    Work.submitted_at >= since,
                )
            )
        ).scalar()
        or 0
    )
    failed = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Work)
                .where(
                    Work.engine_code == code,
                    Work.task_status == "failed",
                    Work.submitted_at >= since,
                )
            )
        ).scalar()
        or 0
    )
    avg_cost = (
        await session.execute(
            select(func.avg(Work.cost_seconds)).where(
                Work.engine_code == code, Work.task_status == "succeeded", Work.submitted_at >= since
            )
        )
    ).scalar()
    return EngineHealthOut(
        recent_success=success,
        recent_fail=failed,
        avg_cost_sec=float(avg_cost) if avg_cost is not None else None,
        circuit_open=False,
        source="db",
    )


def _to_out(engine: Engine, health: EngineHealthOut) -> EngineOut:
    total = health.recent_success + health.recent_fail
    fail_rate = (health.recent_fail / total) if total else 0.0
    return EngineOut(
        id=engine.id,
        code=engine.code,
        name=engine.name,
        provider=engine.provider,
        model_name=engine.model_name,
        resolution=engine.resolution,
        duration=int(engine.duration or 15),
        price_per_sec=Decimal(engine.price_per_sec or 0),
        max_concurrency=int(engine.max_concurrency or 25),
        is_default=bool(engine.is_default),
        status=int(engine.status),
        updated_at=engine.updated_at,
        health=health,
        fail_rate=round(fail_rate, 4),
        is_healthy=not health.circuit_open and fail_rate <= FAIL_RATE_ALERT,
    )


@router.get("", response_model=list[EngineOut], summary="引擎列表（含 Redis 健康指标与兜底统计）")
async def list_engines(
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[EngineOut]:
    engines = (
        await session.execute(select(Engine).order_by(Engine.is_default.desc(), Engine.id.asc()))
    ).scalars().all()

    result: list[EngineOut] = []
    for engine in engines:
        health = await _health_from_redis(engine.code)
        if health is None:
            health = await _health_from_db(session, engine.code)
        result.append(_to_out(engine, health))
    return result


@router.put("/{engine_id}", response_model=EngineOut, summary="编辑引擎（名称/单价/并发上限/启停）")
async def update_engine(
    engine_id: int,
    payload: EngineUpdateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> EngineOut:
    engine = await session.get(Engine, engine_id)
    if engine is None:
        raise AppError("ENGINE_NOT_FOUND", "引擎不存在", status_code=404)

    if payload.status == 0 and engine.is_default == 1:
        raise AppError(
            "ENGINE_DEFAULT_CANNOT_DISABLE",
            f"引擎 {engine.code} 是当前默认引擎，不能直接停用；请先把默认引擎切换到其它引擎",
            status_code=400,
        )

    before = {
        "name": engine.name,
        "price_per_sec": str(engine.price_per_sec),
        "max_concurrency": int(engine.max_concurrency or 0),
        "status": int(engine.status),
    }

    if payload.name is not None:
        engine.name = payload.name.strip()
    if payload.price_per_sec is not None:
        engine.price_per_sec = payload.price_per_sec
    if payload.max_concurrency is not None:
        engine.max_concurrency = payload.max_concurrency
    if payload.status is not None:
        engine.status = payload.status

    await session.flush()
    await sync_engine_config(engine)
    await invalidate_engine_cache()

    after = {
        "name": engine.name,
        "price_per_sec": str(engine.price_per_sec),
        "max_concurrency": int(engine.max_concurrency or 0),
        "status": int(engine.status),
    }
    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="update_engine",
        target_type="engine",
        target_id=engine.id,
        before=before,
        after=after,
        remark=f"更新引擎 {engine.code} 配置",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(engine)

    logger.info("引擎 %s 配置已更新并刷新缓存：%s", engine.code, after)
    health = await _health_from_redis(engine.code) or await _health_from_db(session, engine.code)
    return _to_out(engine, health)


@router.post("/{engine_id}/set-default", response_model=EngineOut, summary="一键切换默认引擎（480P 降级开关）")
async def set_default_engine(
    engine_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> EngineOut:
    target = await session.get(Engine, engine_id)
    if target is None:
        raise AppError("ENGINE_NOT_FOUND", "引擎不存在", status_code=404)
    if target.status != 1:
        # 切默认的同时会把它重新启用（手册：置为 is_default=1, status=1）
        target.status = 1

    engines = (await session.execute(select(Engine))).scalars().all()
    before = {engine.code: int(engine.is_default) for engine in engines}

    for engine in engines:
        engine.is_default = 1 if engine.id == engine_id else 0
    if target.status != 1:
        target.status = 1

    await session.flush()
    for engine in engines:
        await sync_engine_config(engine)
    await invalidate_engine_cache()
    await redis_client.delete(DEFAULT_ENGINE_KEY)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="switch_default_engine",
        target_type="engine",
        target_id=target.id,
        before=before,
        after={"default": target.code, "resolution": target.resolution, "is_default": {target.code: 1}},
        remark=f"切换默认引擎为 {target.code}（{target.resolution}）",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(target)

    logger.info("默认引擎已切换为 %s（%s），缓存已刷新", target.code, target.resolution)
    health = await _health_from_redis(target.code) or await _health_from_db(session, target.code)
    return _to_out(target, health)
