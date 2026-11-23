"""开放时段的请求 / 响应模型。

时间口径：入参允许带时区（前端可能传 `2026-11-23T09:00:00+08:00`），
统一用 `as_naive_utc` 归一成「无时区的 UTC」再入库，与 `time_windows` 的 DATETIME 列一致。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timeutil import as_naive_utc
from app.schemas.common import OptionalUtcDatetime


def _to_naive_utc(value: datetime | None) -> datetime | None:
    return None if value is None else as_naive_utc(value)


class TimeWindowCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime
    status: int = Field(default=1, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("时段名称不能为空")
        return stripped

    @field_validator("start_at", "end_at")
    @classmethod
    def _normalize(cls, value: datetime) -> datetime:
        return as_naive_utc(value)

    @model_validator(mode="after")
    def _check_range(self) -> "TimeWindowCreateIn":
        if self.end_at <= self.start_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class TimeWindowUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: int | None = Field(default=None, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("start_at", "end_at")
    @classmethod
    def _normalize(cls, value: datetime | None) -> datetime | None:
        return _to_naive_utc(value)


class TimeWindowExtendIn(BaseModel):
    """临时延长：给 `new_end_at` 或 `extend_minutes` 二选一，且必须 confirm=true。"""

    new_end_at: datetime | None = None
    extend_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    confirm: bool = Field(default=False, description="二次确认，必须为 true")
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("new_end_at")
    @classmethod
    def _normalize(cls, value: datetime | None) -> datetime | None:
        return _to_naive_utc(value)


class TimeWindowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    start_at: OptionalUtcDatetime = None
    end_at: OptionalUtcDatetime = None
    status: int
    remark: str | None = None
    created_at: OptionalUtcDatetime = None
    updated_at: OptionalUtcDatetime = None
    is_active: bool = Field(False, description="当前服务器时间是否落在该时段内")
    remaining_sec: int | None = Field(None, description="该时段剩余秒数；不在时段内为 null")


class TimeWindowListOut(BaseModel):
    server_time: OptionalUtcDatetime = None
    active_window_id: int | None = Field(None, description="当前生效的时段 ID，没有则为 null")
    total: int
    items: list[TimeWindowOut]
