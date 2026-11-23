"""配额表 quotas（并发防超发核心表）。"""

from __future__ import annotations

from sqlalchemy import Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, TinyInt, UpdatedOnlyMixin


class Quota(Base, UpdatedOnlyMixin):
    __tablename__ = "quotas"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(BigIntUnsigned, nullable=False)
    question_id: Mapped[int] = mapped_column(BigIntUnsigned, nullable=False)
    quota_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    quota_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="已用次数（含生成中）")
    adjusted_flag: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("0"), comment="是否被管理员手动调整过")

    __table_args__ = (
        # 与 schema.sql 的 uk_candidate_question 对应（唯一约束在 DDL 中定义，此处仅声明元数据）
        {"comment": "考生题目配额表"},
    )

    def __repr__(self) -> str:
        return f"<Quota candidate={self.candidate_id} question={self.question_id} {self.quota_used}/{self.quota_total}>"
