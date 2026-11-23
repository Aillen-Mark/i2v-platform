"""考生端作品的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import OptionalUtcDatetime

WORK_STATUS_TEXT: dict[str, str] = {
    "draft": "草稿",
    "queued": "排队中",
    "running": "生成中",
    "succeeded": "可下载",
    "failed": "失败",
}


class WorkOut(BaseModel):
    id: int
    task_no: str
    candidate_id: int
    question_id: int
    seq: int | None = None
    prompt: str
    aspect_ratio: str | None = None
    task_status: str
    status_text: str = Field(description="任务状态中文说明")
    engine_code: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    submitted_at: OptionalUtcDatetime = None
    finished_at: OptionalUtcDatetime = None
    created_at: OptionalUtcDatetime = None
    # 排队信息（仅 queued 时给出）
    queue_ahead: int | None = Field(None, description="前面还有多少个排队任务")
    estimated_wait_sec: int | None = Field(None, description="预计等待秒数")
    # 该题剩余次数（方便前端即时刷新）
    quota_remaining: int | None = None


class SubmitResultOut(BaseModel):
    id: int
    task_no: str
    task_status: str
    status_text: str
    seq: int | None = None
    engine_code: str | None = None
    quota_remaining: int | None = None
    queue_ahead: int | None = None
    estimated_wait_sec: int | None = None
    idempotent: bool = Field(False, description="true 表示重复提交，未重复扣配额")
    message: str = "已提交生成"
