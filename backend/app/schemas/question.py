"""题目的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import OptionalUtcDatetime


def _clean_examples(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned = [item.strip() for item in value if item and item.strip()]
    if len(cleaned) > 3:
        raise ValueError("示例提示词最多 3 条")
    return cleaned or None


class QuestionCreateIn(BaseModel):
    code: str | None = Field(default=None, max_length=32, description="留空则自动生成 Q1/Q2…")
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    ref_image_key: str | None = Field(default=None, max_length=255)
    prompt_examples: list[str] | None = Field(default=None, description="示例提示词，最多 3 条")
    default_quota: int = Field(default=2, ge=0, le=100)
    sort_order: int = Field(default=0)
    status: int = Field(default=1, ge=0, le=1)

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped

    @field_validator("prompt_examples")
    @classmethod
    def _examples(cls, value: list[str] | None) -> list[str] | None:
        return _clean_examples(value)


class QuestionUpdateIn(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1)
    ref_image_key: str | None = Field(default=None, max_length=255)
    prompt_examples: list[str] | None = None
    default_quota: int | None = Field(default=None, ge=0, le=100)
    sort_order: int | None = None
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("prompt_examples")
    @classmethod
    def _examples(cls, value: list[str] | None) -> list[str] | None:
        return _clean_examples(value)


class QuestionSortIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200, description="按期望顺序排列的题目 ID")


class QuestionAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    description: str
    ref_image_key: str | None = None
    ref_image_url: str | None = None
    prompt_examples: list[str] | None = None
    default_quota: int
    sort_order: int
    status: int
    created_at: OptionalUtcDatetime = None
    updated_at: OptionalUtcDatetime = None


class QuestionPublicOut(BaseModel):
    """考生端题目：带本人配额余量。"""

    id: int
    code: str
    title: str
    description: str
    ref_image_url: str | None = None
    prompt_examples: list[str] | None = None
    default_quota: int
    quota_total: int = Field(description="该题分配给本人的总次数")
    quota_used: int = Field(description="已用次数（含生成中）")
    remaining: int = Field(description="剩余次数")
    adjusted_flag: bool = Field(description="是否被管理员手动调整过")
