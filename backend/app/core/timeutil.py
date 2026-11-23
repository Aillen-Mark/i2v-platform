"""时间工具：数据库统一存 UTC（naive DATETIME），接口统一返回 ISO 8601 带时区。

铁律 10（CLAUDE.md）：数据库存 UTC，接口返回 ISO 8601 带时区，前端按 Asia/Shanghai 展示。
"""

from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc


def utcnow() -> datetime:
    """当前 UTC 时间（naive，用于写入 DATETIME 列）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def to_iso_utc(value: datetime | None) -> str | None:
    """naive(UTC) / aware datetime → '2026-11-23T10:38:17+00:00'。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def as_naive_utc(value: datetime) -> datetime:
    """接口收到的 aware datetime → 入库用的 naive UTC。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
