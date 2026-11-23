"""公共类型：UTC 时间序列化、消息体。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, PlainSerializer

from app.core.timeutil import to_iso_utc

# 铁律 10：接口返回 ISO 8601 带时区，数据库存的是 naive UTC
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(to_iso_utc, return_type=str, when_used="json"),
]

OptionalUtcDatetime = Annotated[
    Optional[datetime],
    PlainSerializer(to_iso_utc, return_type=Optional[str], when_used="json"),
]


class Message(BaseModel):
    message: str
