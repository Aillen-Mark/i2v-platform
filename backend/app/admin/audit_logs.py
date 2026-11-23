"""管理端操作日志检索（只读，按时间倒序）。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import get_current_admin
from app.core.timeutil import as_naive_utc
from app.models import Admin, AuditLog
from app.schemas.audit import AuditLogOut, AuditLogPageOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/audit-logs", tags=["管理端 · 操作日志"])


@router.get("", response_model=AuditLogPageOut, summary="操作日志检索（多条件 + 分页）")
async def list_audit_logs(
    admin_username: str | None = Query(None, description="操作人账号（模糊匹配）"),
    action: str | None = Query(None, description="动作类型，如 adjust_quota / switch_default_engine"),
    target_type: str | None = Query(None, description="对象类型，如 candidate / quota / engine"),
    start_at: datetime | None = Query(None, description="起始时间（含），支持带时区"),
    end_at: datetime | None = Query(None, description="结束时间（含），支持带时区"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AuditLogPageOut:
    conditions = []
    if admin_username:
        conditions.append(AuditLog.admin_username.like(f"%{admin_username.strip()}%"))
    if action:
        conditions.append(AuditLog.action == action.strip())
    if target_type:
        conditions.append(AuditLog.target_type == target_type.strip())
    if start_at is not None:
        conditions.append(AuditLog.created_at >= as_naive_utc(start_at))
    if end_at is not None:
        conditions.append(AuditLog.created_at <= as_naive_utc(end_at))

    total = int(
        (
            await session.execute(select(func.count()).select_from(AuditLog).where(*conditions))
        ).scalar()
        or 0
    )

    rows = (
        await session.execute(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    items = [
        AuditLogOut(
            id=log.id,
            admin_id=log.admin_id,
            admin_username=log.admin_username,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            before_val=log.before_val,
            after_val=log.after_val,
            remark=log.remark,
            ip_address=log.ip_address,
            created_at=log.created_at,
        )
        for log in rows
    ]
    return AuditLogPageOut(total=total, page=page, size=size, items=items)
