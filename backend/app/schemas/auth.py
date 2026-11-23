"""认证相关的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import OptionalUtcDatetime


class CandidateLoginIn(BaseModel):
    account: str = Field(min_length=1, max_length=64, description="学号 / 考号")
    password: str = Field(min_length=1, max_length=128)


class AdminLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CandidateMeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account: str
    name: str
    status: int
    last_login_at: OptionalUtcDatetime = None
    # 总配额汇总：各启用题目的 quota_total / quota_used 之和
    total_quota: int = Field(0, description="所有启用题目的总次数之和")
    total_used: int = Field(0, description="所有启用题目的已用次数之和")
    total_remaining: int = Field(0, description="所有启用题目的剩余次数之和")
    # 开放时段快照（M3 起为提交网关的强校验依据，前端据此置灰提交按钮）
    in_window: bool = Field(False, description="当前是否处于任一启用时段内")
    current_window_remaining_sec: int | None = Field(None, description="当前时段剩余秒数；不在时段内为 null")
    next_window_start: OptionalUtcDatetime = Field(None, description="下一次开放时间；无启用时段时为 null")
    current_window_end: OptionalUtcDatetime = Field(None, description="当前时段结束时间；不在时段内为 null")


class AdminMeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    last_login_at: OptionalUtcDatetime = None


class LogoutOut(BaseModel):
    message: str = "已退出登录"
