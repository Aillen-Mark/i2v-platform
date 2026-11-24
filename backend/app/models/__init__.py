"""ORM 模型包，对应 docs/schema.sql。"""

from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.base import Base, BigIntUnsigned, CreatedUpdatedMixin, TinyInt, UpdatedOnlyMixin
from app.models.candidate import Candidate
from app.models.engine import Engine
from app.models.generation_attempt import GenerationAttempt
from app.models.generation_execution import GenerationExecution
from app.models.generation_outbox import GenerationOutbox
from app.models.question import Question
from app.models.quota import Quota
from app.models.time_window import TimeWindow
from app.models.work import (
    WORK_STATUS_DRAFT,
    WORK_STATUS_FAILED,
    WORK_STATUS_QUEUED,
    WORK_STATUS_RUNNING,
    WORK_STATUS_SUCCEEDED,
    Work,
)

__all__ = [
    "Admin",
    "AuditLog",
    "Base",
    "BigIntUnsigned",
    "Candidate",
    "CreatedUpdatedMixin",
    "Engine",
    "GenerationAttempt",
    "GenerationExecution",
    "GenerationOutbox",
    "Question",
    "Quota",
    "TinyInt",
    "TimeWindow",
    "UpdatedOnlyMixin",
    "Work",
    "WORK_STATUS_DRAFT",
    "WORK_STATUS_QUEUED",
    "WORK_STATUS_RUNNING",
    "WORK_STATUS_SUCCEEDED",
    "WORK_STATUS_FAILED",
]
