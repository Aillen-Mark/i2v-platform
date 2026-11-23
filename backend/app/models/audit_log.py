"""审计与配额变更日志表 audit_logs。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigIntUnsigned, nullable=False, comment="系统自动退还时记为 0")
    admin_username: Mapped[str] = mapped_column(String(64), nullable=False, comment="冗余操作人账号，防删号丢失")
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="import_users / reset_pwd / refund_quota ...")
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="candidate / quota / work / engine / question")
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_val: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    after_val: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r} target={self.target_type}:{self.target_id}>"
