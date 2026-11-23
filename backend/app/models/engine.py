"""模型引擎配置表 engines。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, TinyInt, UpdatedOnlyMixin


class Engine(Base, UpdatedOnlyMixin):
    __tablename__ = "engines"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, comment="对外代号，如 wq-v1")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="后台展示名称")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'minimax'"))
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="上游真实模型名")
    resolution: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'768P'"))
    duration: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("15"))
    price_per_sec: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, server_default=text("0.0000"))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("25"))
    is_default: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("0"))
    status: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("1"), comment="1 启用 / 0 停用")

    def __repr__(self) -> str:
        return f"<Engine code={self.code!r} model={self.model_name!r} {self.resolution}>"
