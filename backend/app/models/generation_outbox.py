"""Durable delivery record for a submitted generation Work."""

from datetime import datetime

from sqlalchemy import CHAR, Index, Integer, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned


class GenerationOutbox(Base):
    __tablename__ = "generation_outbox"
    __table_args__ = (
        Index("idx_outbox_due", "received_at", "dispatch_after", "work_id"),
        Index("idx_outbox_created", "received_at", "created_at"),
    )

    work_id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    dispatch_after: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    lease_token: Mapped[str | None] = mapped_column(CHAR(32))
    leased_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    received_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    last_error: Mapped[str | None] = mapped_column(String(64))
