"""配额调控的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import OptionalUtcDatetime


class QuotaRowOut(BaseModel):
    id: int
    candidate_id: int
    account: str
    name: str
    question_id: int
    question_code: str
    question_title: str
    quota_total: int
    quota_used: int
    quota_remaining: int
    adjusted_flag: bool
    updated_at: OptionalUtcDatetime = None


class QuotaPageOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[QuotaRowOut]


class BatchByQuestionIn(BaseModel):
    question_id: int = Field(gt=0)
    quota_total: int = Field(ge=0, le=100)
    force_confirm: bool = Field(default=False, description="存在已用次数大于目标值的考生时必须为 true")
    remark: str | None = Field(default=None, max_length=255)


class QuotaItemIn(BaseModel):
    question_id: int = Field(gt=0)
    quota_total: int = Field(ge=0, le=100)


class ByCandidateIn(BaseModel):
    """按人设置：`items` 逐题设置 与 `uniform_quota` 一键统一，二选一。"""

    items: list[QuotaItemIn] | None = None
    uniform_quota: int | None = Field(default=None, ge=0, le=100)
    force_confirm: bool = Field(default=False)
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _check_mode(self) -> "ByCandidateIn":
        if self.items and self.uniform_quota is not None:
            raise ValueError("items 与 uniform_quota 只能二选一")
        if not self.items and self.uniform_quota is None:
            raise ValueError("必须提供 items 或 uniform_quota")
        return self


class BatchAdjustIn(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=1000)
    question_id: int = Field(gt=0)
    delta: int = Field(description="可正可负，如 +1 / -1", ge=-100, le=100)
    force_confirm: bool = Field(default=False)
    remark: str | None = Field(default=None, max_length=255)


class QuotaChangeOut(BaseModel):
    updated: int = Field(description="实际更新的配额行数")
    blocked: int = Field(default=0, description="因下调保护被跳过的行数（force_confirm 时会被覆盖）")
    items: list[QuotaRowOut] = Field(default_factory=list)
