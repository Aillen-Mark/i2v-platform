"""Durable record of one MiniMax create attempt."""

from datetime import datetime

from sqlalchemy import CHAR, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, TinyInt


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("work_id", "attempt_no", name="uk_attempt_work_no"),
        UniqueConstraint("request_id", name="uk_attempt_request"),
        UniqueConstraint("provider", "provider_task_id", name="uk_attempt_provider_task"),
        Index("idx_attempt_due", "status", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    work_id: Mapped[int] = mapped_column(BigIntUnsigned)
    attempt_no: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(CHAR(32))
    provider: Mapped[str] = mapped_column(String(32))
    provider_task_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24))
    retry_create: Mapped[int] = mapped_column(TinyInt, server_default=text("0"))
    request_started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    response_received_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    last_checked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    next_check_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
