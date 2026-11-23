"""开放时段服务：判断当前是否在开放时段内，给出下一次开放时间与剩余秒数。

时间基准：全项目统一用「无时区的 UTC」（`timeutil.utcnow()`），
`time_windows.start_at / end_at` 也是 naive UTC，比较时不引入任何本地时区偏移。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.models import TimeWindow


def _as_dict(window: TimeWindow) -> dict[str, object]:
    return {
        "id": window.id,
        "name": window.name,
        "start_at": window.start_at,
        "end_at": window.end_at,
    }


async def current_window_snapshot(session: AsyncSession, now: datetime | None = None) -> dict[str, object]:
    """返回 {in_window, current_window, current_window_remaining_sec, next_window_start, server_time}。"""
    moment = now or utcnow()
    windows = (
        await session.execute(
            select(TimeWindow).where(TimeWindow.status == 1).order_by(TimeWindow.start_at.asc())
        )
    ).scalars().all()

    active: TimeWindow | None = None
    upcoming: TimeWindow | None = None
    for window in windows:
        if window.start_at <= moment <= window.end_at:
            active = window
            break
    for window in windows:
        if window.start_at > moment:
            upcoming = window
            break

    remaining = None
    if active is not None:
        remaining = max(0, int((active.end_at - moment).total_seconds()))

    return {
        "in_window": active is not None,
        "current_window": _as_dict(active) if active else None,
        "current_window_remaining_sec": remaining,
        "next_window_start": upcoming.start_at if upcoming else None,
        "server_time": moment,
    }


async def is_in_window(session: AsyncSession, now: datetime | None = None) -> bool:
    """当前是否处于任一启用时段内（提交网关的强校验）。"""
    moment = now or utcnow()
    row = (
        await session.execute(
            select(TimeWindow.id)
            .where(TimeWindow.status == 1, TimeWindow.start_at <= moment, TimeWindow.end_at >= moment)
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None
