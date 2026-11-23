"""题目表 questions。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, CreatedUpdatedMixin, TinyInt


class Question(Base, CreatedUpdatedMixin):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, comment="题号标识，如 Q1")
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    ref_image_key: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="参考图对象存储 Key")
    prompt_examples: Mapped[Any | None] = mapped_column(JSON, nullable=True, comment='["示例1","示例2","示例3"]')
    default_quota: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("1"), comment="1 启用 / 0 停用（软删除）")

    def __repr__(self) -> str:
        return f"<Question id={self.id} code={self.code!r}>"
