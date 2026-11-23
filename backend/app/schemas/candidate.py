"""考生管理的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import OptionalUtcDatetime


class CandidateCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    account: str | None = Field(default=None, max_length=64, description="留空则按规则自动生成")
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("姓名不能为空")
        return stripped

    @field_validator("account")
    @classmethod
    def _strip_account(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CandidateUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    account: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("name", "account")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account: str
    name: str
    status: int
    remark: str | None = None
    last_login_at: OptionalUtcDatetime = None
    last_login_ip: str | None = None
    created_at: OptionalUtcDatetime = None
    quota_total_sum: int = 0
    quota_used_sum: int = 0
    init_pwd_viewable: bool = False


class CandidatePageOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[CandidateOut]


class ImportRowErrorOut(BaseModel):
    row: int = Field(description="Excel 行号（表头为第 1 行）")
    name: str | None = None
    account: str | None = None
    reason: str


class ImportResultOut(BaseModel):
    batch_no: str
    total_rows: int = Field(description="有效数据行总数（不含表头与空行）")
    success_count: int
    failed_count: int
    errors: list[ImportRowErrorOut]
    elapsed_ms: int


class BatchIdsIn(BaseModel):
    """批量操作入参：M2 手册用 `candidate_ids`，同时兼容早期的 `ids`。"""

    ids: list[int] = Field(default_factory=list, max_length=1000)
    candidate_ids: list[int] | None = Field(default=None, max_length=1000)

    @property
    def id_list(self) -> list[int]:
        merged = list(self.ids or []) + list(self.candidate_ids or [])
        unique: list[int] = []
        for item in merged:
            if item not in unique:
                unique.append(int(item))
        return unique

    @model_validator(mode="after")
    def _require_ids(self) -> "BatchIdsIn":
        if not self.id_list:
            raise ValueError("ids 或 candidate_ids 至少提供一个")
        return self


class BatchStatusIn(BatchIdsIn):
    status: int = Field(ge=0, le=1)


class ResetPasswordOut(BaseModel):
    id: int
    account: str
    name: str
    password: str = Field(description="新初始密码，仅本次响应可见")


class BatchResetOut(BaseModel):
    items: list[ResetPasswordOut]


class PasswordSlipOut(BaseModel):
    id: int
    account: str
    name: str
    password: str | None = Field(description="初始密码；已过期时为 null")
    expire_at: OptionalUtcDatetime = None
    expired: bool
