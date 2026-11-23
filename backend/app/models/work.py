"""作品与生成任务表 works（业务端与生成模块的核心交接表）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntUnsigned, CreatedUpdatedMixin, TinyInt

# 状态机（docs/handoff-contract.md）：
# draft → queued → running → succeeded / failed，或 queued → failed；终态不可再变。
WORK_STATUS_DRAFT = "draft"
WORK_STATUS_QUEUED = "queued"
WORK_STATUS_RUNNING = "running"
WORK_STATUS_SUCCEEDED = "succeeded"
WORK_STATUS_FAILED = "failed"


class Work(Base, CreatedUpdatedMixin):
    __tablename__ = "works"

    id: Mapped[int] = mapped_column(BigIntUnsigned, primary_key=True, autoincrement=True)
    task_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="对外任务号，防遍历")
    candidate_id: Mapped[int] = mapped_column(BigIntUnsigned, nullable=False)
    question_id: Mapped[int] = mapped_column(BigIntUnsigned, nullable=False)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="该生该题第几次提交，draft 阶段为空")
    image_key: Mapped[str] = mapped_column(String(255), nullable=False, comment="首帧图对象存储 Key")
    aspect_ratio: Mapped[str | None] = mapped_column(String(16), server_default=text("'16:9'"))
    prompt: Mapped[str] = mapped_column(String(500), nullable=False)
    task_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'draft'"), comment="draft/queued/running/succeeded/failed"
    )
    engine_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upstream_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    video_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_sec: Mapped[int | None] = mapped_column(Integer, server_default=text("15"))
    cost_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    quota_refunded: Mapped[int] = mapped_column(TinyInt, nullable=False, server_default=text("0"), comment="1 已退 / 0 未退")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Work id={self.id} task_no={self.task_no!r} status={self.task_status}>"
