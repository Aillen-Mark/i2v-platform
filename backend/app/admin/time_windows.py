"""管理端开放时段：增删改查 + 临时延长（全部写审计）。"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.core.security import utcnow
from app.models import Admin, TimeWindow
from app.schemas.common import Message
from app.schemas.time_window import (
    TimeWindowCreateIn,
    TimeWindowExtendIn,
    TimeWindowListOut,
    TimeWindowOut,
    TimeWindowUpdateIn,
)
from app.services.audit import write_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/time-windows", tags=["管理端 · 开放时段"])


def _to_out(window: TimeWindow, now) -> TimeWindowOut:
    is_active = window.status == 1 and window.start_at <= now <= window.end_at
    remaining = max(0, int((window.end_at - now).total_seconds())) if is_active else None
    return TimeWindowOut(
        id=window.id,
        name=window.name,
        start_at=window.start_at,
        end_at=window.end_at,
        status=window.status,
        remark=window.remark,
        created_at=window.created_at,
        updated_at=window.updated_at,
        is_active=is_active,
        remaining_sec=remaining,
    )


def _snapshot(window: TimeWindow) -> dict[str, object]:
    return {
        "name": window.name,
        "start_at": window.start_at.isoformat(sep=" ") if window.start_at else None,
        "end_at": window.end_at.isoformat(sep=" ") if window.end_at else None,
        "status": window.status,
        "remark": window.remark,
    }


@router.get("", response_model=TimeWindowListOut, summary="时段列表（按开始时间升序，含当前生效标识）")
async def list_time_windows(
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> TimeWindowListOut:
    now = utcnow()
    windows = (
        await session.execute(select(TimeWindow).order_by(TimeWindow.start_at.asc(), TimeWindow.id.asc()))
    ).scalars().all()

    items = [_to_out(window, now) for window in windows]
    active_id = next((item.id for item in items if item.is_active), None)
    return TimeWindowListOut(server_time=now, active_window_id=active_id, total=len(items), items=items)


@router.post("", response_model=TimeWindowOut, summary="新增时段")
async def create_time_window(
    payload: TimeWindowCreateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> TimeWindowOut:
    if payload.end_at <= payload.start_at:
        raise AppError("INVALID_WINDOW_RANGE", "结束时间必须晚于开始时间", status_code=422)

    window = TimeWindow(
        name=payload.name,
        start_at=payload.start_at,
        end_at=payload.end_at,
        status=payload.status,
        remark=payload.remark,
    )
    session.add(window)
    await session.flush()

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="create_time_window",
        target_type="time_window",
        target_id=window.id,
        after=_snapshot(window),
        remark=payload.remark or f"新增时段 {window.name}",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(window)
    logger.info("新增时段 %s：%s ~ %s", window.id, window.start_at, window.end_at)
    return _to_out(window, utcnow())


@router.put("/{window_id}", response_model=TimeWindowOut, summary="编辑时段（含启停）")
async def update_time_window(
    window_id: int,
    payload: TimeWindowUpdateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> TimeWindowOut:
    window = await session.get(TimeWindow, window_id)
    if window is None:
        raise AppError("TIME_WINDOW_NOT_FOUND", "时段不存在", status_code=404)

    before = _snapshot(window)
    if payload.name is not None:
        window.name = payload.name.strip()
    if payload.start_at is not None:
        window.start_at = payload.start_at
    if payload.end_at is not None:
        window.end_at = payload.end_at
    if payload.status is not None:
        window.status = payload.status
    if payload.remark is not None:
        window.remark = payload.remark

    if window.end_at <= window.start_at:
        raise AppError("INVALID_WINDOW_RANGE", "结束时间必须晚于开始时间", status_code=422)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="update_time_window",
        target_type="time_window",
        target_id=window.id,
        before=before,
        after=_snapshot(window),
        remark=payload.remark or "编辑开放时段",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(window)
    return _to_out(window, utcnow())


@router.delete("/{window_id}", response_model=Message, summary="删除时段")
async def delete_time_window(
    window_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Message:
    window = await session.get(TimeWindow, window_id)
    if window is None:
        raise AppError("TIME_WINDOW_NOT_FOUND", "时段不存在", status_code=404)

    before = _snapshot(window)
    await session.delete(window)
    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="delete_time_window",
        target_type="time_window",
        target_id=window_id,
        before=before,
        after={"deleted": True},
        remark=f"删除时段 {before['name']}",
        ip=client_ip(request),
    )
    await session.commit()
    return Message(message=f"时段 {before['name']} 已删除")


@router.post("/{window_id}/extend", response_model=TimeWindowOut, summary="临时延长时段（需二次确认）")
async def extend_time_window(
    window_id: int,
    payload: TimeWindowExtendIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> TimeWindowOut:
    if not payload.confirm:
        raise AppError(
            "CONFIRM_REQUIRED",
            "延长时段必须二次确认（confirm=true）",
            status_code=400,
        )

    window = await session.get(TimeWindow, window_id)
    if window is None:
        raise AppError("TIME_WINDOW_NOT_FOUND", "时段不存在", status_code=404)

    if payload.new_end_at is not None:
        target = payload.new_end_at
    elif payload.extend_minutes:
        target = window.end_at + timedelta(minutes=int(payload.extend_minutes))
    else:
        raise AppError("INVALID_EXTEND", "必须提供 new_end_at 或 extend_minutes", status_code=400)

    if target <= window.end_at:
        raise AppError(
            "INVALID_EXTEND",
            "新的结束时间必须晚于原结束时间",
            status_code=400,
            detail={"current_end_at": window.end_at.isoformat(sep=" ")},
        )

    before = _snapshot(window)
    window.end_at = target
    after = _snapshot(window)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="extend_window",
        target_type="time_window",
        target_id=window.id,
        before=before,
        after=after,
        remark=payload.remark or f"临时延长时段至 {target.isoformat(sep=' ')}",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(window)
    logger.info("时段 %s 延长至 %s", window.id, window.end_at)
    return _to_out(window, utcnow())
