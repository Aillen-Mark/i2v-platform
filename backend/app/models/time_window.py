"""开放时段表 time_windows。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, CreatedUpdatedMixin, TinyInt


class TimeWindow(Base, CreatedUpdatedMixin):
    __tablename__ = "time_windows"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="时段名称，如：上午场")
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("1"), comment="1 启用 / 0 停用")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<TimeWindow id={self.id} name={self.name!r} {self.start_at}~{self.end_at}>"
