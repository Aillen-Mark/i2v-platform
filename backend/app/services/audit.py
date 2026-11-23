"""审计日志写入（铁律 9）：导入考生、重置密码、调整配额、增删题目、修改/延长时段、启停模型、失败退还。

约定：本函数只 add 到调用方的事务里，由调用方统一 commit —— 保证「业务变更」与「审计记录」同生共死。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    admin_id: int,
    admin_username: str,
    action: str,
    target_type: str,
    target_id: str | int | None = None,
    before: Any = None,
    after: Any = None,
    remark: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    """写一条审计记录（不提交事务）。"""
    log = AuditLog(
        admin_id=int(admin_id),
        admin_username=admin_username[:64],
        action=action[:64],
        target_type=target_type[:32],
        target_id=None if target_id is None else str(target_id)[:64],
        before_val=before,
        after_val=after,
        remark=(remark or "")[:255] or None,
        ip_address=(ip or "")[:45] or None,
    )
    session.add(log)
    return log
