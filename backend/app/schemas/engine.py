"""模型引擎配置的请求 / 响应模型。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import OptionalUtcDatetime


class EngineHealthOut(BaseModel):
    recent_success: int = 0
    recent_fail: int = 0
    avg_cost_sec: float | None = None
    circuit_open: bool = False
    source: str = Field("redis", description="指标来源：redis / db（最近 1 小时兜底统计）")


class EngineOut(BaseModel):
    id: int
    code: str
    name: str
    provider: str
    model_name: str
    resolution: str
    duration: int
    price_per_sec: Decimal
    max_concurrency: int
    is_default: bool
    status: int
    updated_at: OptionalUtcDatetime = None
    health: EngineHealthOut = Field(default_factory=EngineHealthOut)
    fail_rate: float = Field(0.0, description="最近 1 小时失败率（0~1）")
    is_healthy: bool = Field(True, description="失败率 >20% 或熔断时为 false，前端标红")


class EngineUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    price_per_sec: Decimal | None = Field(default=None, ge=0, le=Decimal("10000"))
    max_concurrency: int | None = Field(default=None, ge=1, le=500)
    status: int | None = Field(default=None, ge=0, le=1)
