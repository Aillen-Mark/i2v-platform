"""MySQL ownership and recovery checkpoint for a Work."""

from datetime import datetime

from sqlalchemy import CHAR, Index, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, TinyInt


class GenerationExecution(Base):
    __tablename__ = "generation_executions"
    __table_args__ = (Index("idx_execution_recover", "recover_after", "lease_expires_at", "work_id"),)

    work_id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=False)
    lease_owner: Mapped[str | None] = mapped_column(CHAR(32))
    lease_generation: Mapped[int] = mapped_column(BigIntUnsigned, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    recover_after: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    legacy_uncertain: Mapped[int] = mapped_column(TinyInt, server_default=text("0"))
    artifact_key: Mapped[str | None] = mapped_column(String(255))
    artifact_size: Mapped[int | None] = mapped_column(BigIntUnsigned)
    artifact_sha256: Mapped[str | None] = mapped_column(CHAR(64))
