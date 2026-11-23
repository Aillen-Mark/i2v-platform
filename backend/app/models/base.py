"""ORM 基类与公共列类型（与 docs/schema.sql 严格对应）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# MySQL 无符号 bigint / tinyint 变体，其余方言退化为通用类型
BigIntUnsigned = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
TinyInt = SmallInteger().with_variant(mysql.TINYINT(), "mysql")

# 常用列类型别名
Varchar64 = String(64)
Varchar128 = String(128)
Varchar255 = String(255)
Varchar45 = String(45)
IntColumn = Integer


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。

    eager_defaults：INSERT 后立刻取回 server_default（created_at / updated_at 等）。
    MySQL 不支持 RETURNING，SQLAlchemy 会在 flush 内补一条 SELECT，
    避免异步上下文里访问未加载属性触发 MissingGreenlet。
    """

    __mapper_args__ = {"eager_defaults": True}


class CreatedUpdatedMixin:
    """created_at / updated_at：由 MySQL 侧默认值与 ON UPDATE 维护。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class UpdatedOnlyMixin:
    """只有 updated_at 的表（quotas / engines）。"""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
