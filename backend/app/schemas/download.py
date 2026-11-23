"""下载与作品列表的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import OptionalUtcDatetime


class WorkItemOut(BaseModel):
    id: int
    task_no: str
    question_id: int
    question_code: str
    question_title: str
    seq: int | None = None
    task_status: str
    status_text: str
    prompt: str
    engine_code: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    duration_sec: int | None = None
    file_size: int | None = None
    created_at: OptionalUtcDatetime = None
    submitted_at: OptionalUtcDatetime = None
    finished_at: OptionalUtcDatetime = None
    preview_url: str | None = Field(None, description="成片预览签名链接（30 分钟）")
    download_ready: bool = Field(False, description="是否可下载（succeeded 且对象存在）")


class QuestionGroupOut(BaseModel):
    question_id: int
    question_code: str
    question_title: str
    total: int
    works: list[WorkItemOut]


class MyWorksOut(BaseModel):
    total: int
    groups: list[QuestionGroupOut] = Field(default_factory=list)
    items: list[WorkItemOut] = Field(default_factory=list, description="未分组的平铺列表（按创建时间倒序）")


class DownloadUrlOut(BaseModel):
    download_url: str
    filename: str
    expires_in: int = Field(1800, description="签名有效期（秒）")
    file_size: int | None = None
