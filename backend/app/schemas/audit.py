"""操作日志的响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.common import OptionalUtcDatetime


class AuditLogOut(BaseModel):
    id: int
    admin_id: int
    admin_username: str
    action: str
    target_type: str
    target_id: str | None = None
    before_val: Any = None
    after_val: Any = None
    remark: str | None = None
    ip_address: str | None = None
    created_at: OptionalUtcDatetime = None


class AuditLogPageOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AuditLogOut]
