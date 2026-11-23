"""5 维度用量统计的响应模型。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import OptionalUtcDatetime


class OverviewOut(BaseModel):
    candidates_total: int = 0
    works_total: int = Field(0, description="已提交作品总数（不含 draft）")
    succeeded: int = 0
    failed: int = 0
    pending: int = Field(0, description="排队中 + 生成中")
    success_rate: float = Field(0.0, description="成功率百分比（0~100，两位小数）")
    total_duration_sec: int = Field(0, description="成功作品累计生成时长（秒）")
    estimated_cost: Decimal = Field(Decimal("0"), description="预估费用 = SUM(duration_sec × price_per_sec)")


class CandidateStatOut(BaseModel):
    candidate_id: int
    account: str
    name: str
    status: int
    quota_total: int = 0
    quota_used: int = 0
    quota_remaining: int = 0
    success_count: int = 0
    failed_count: int = 0
    last_active_at: OptionalUtcDatetime = None


class CandidateStatPageOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[CandidateStatOut]


class QuestionStatOut(BaseModel):
    question_id: int
    code: str
    title: str
    status: int
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    fail_rate: float = Field(0.0, description="失败率百分比")
    avg_cost_sec: float | None = None


class EngineStatOut(BaseModel):
    code: str
    name: str
    resolution: str
    calls: int = 0
    succeeded: int = 0
    total_duration_sec: int = 0
    share_rate: float = Field(0.0, description="调用占比百分比")
    cost: Decimal = Field(Decimal("0"), description="折算费用 = SUM(duration_sec × price_per_sec)")


class DayStatOut(BaseModel):
    date: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    fail_rate: float = 0.0


class VideoLinkOut(BaseModel):
    filename: str
    account: str
    question_code: str
    download_url: str
    expires_in: int = 1800
