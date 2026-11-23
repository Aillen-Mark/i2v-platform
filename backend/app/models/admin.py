"""管理员表 admins。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, TinyInt


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'admin'"), comment="admin / super_admin")
    status: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("1"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    def __repr__(self) -> str:
        return f"<Admin id={self.id} username={self.username!r} role={self.role}>"
