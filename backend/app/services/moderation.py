"""内容审核接入层（decisions.md 第 6 条）：抽象成统一接口，默认 mock 全部通过。

真实云厂商接入时只改 `.env`：
    MODERATION_PROVIDER=http
    MODERATION_ENDPOINT=https://your-moderation-gateway.example.com/v1/image/moderate

http 提供方约定：POST JSON `{"filename","content_type","data_b64"}`，
返回 `{"passed": bool, "reason": str|null}`；非 2xx 或结构不符按「拒绝」处理（审核失败不放行）。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModerationResult:
    passed: bool
    reason: str | None
    provider: str


async def _moderate_mock(data: bytes, filename: str, content_type: str) -> ModerationResult:
    return ModerationResult(passed=True, reason=None, provider="mock")


async def _moderate_http(data: bytes, filename: str, content_type: str) -> ModerationResult:
    settings = get_settings()
    if not settings.MODERATION_ENDPOINT:
        raise RuntimeError("MODERATION_PROVIDER=http 但未配置 MODERATION_ENDPOINT")

    payload = {
        "filename": filename,
        "content_type": content_type,
        "data_b64": base64.b64encode(data).decode("ascii"),
    }
    async with httpx.AsyncClient(timeout=settings.MODERATION_TIMEOUT) as client:
        response = await client.post(settings.MODERATION_ENDPOINT, json=payload)
    if response.status_code != 200:
        logger.error("审核服务返回 %s：%s", response.status_code, response.text[:300])
        return ModerationResult(passed=False, reason="审核服务不可用，请稍后重试", provider="http")

    body = response.json()
    passed = bool(body.get("passed"))
    reason = body.get("reason")
    return ModerationResult(passed=passed, reason=None if passed else (reason or "内容未通过审核"), provider="http")


async def moderate_image(data: bytes, filename: str, content_type: str) -> ModerationResult:
    """审核一张待上传图片；provider 未知时按拒绝处理，避免「配置写错就全放行」。"""
    provider = (get_settings().MODERATION_PROVIDER or "mock").strip().lower()
    if provider == "mock":
        return await _moderate_mock(data, filename, content_type)
    if provider == "http":
        return await _moderate_http(data, filename, content_type)
    logger.error("未知的 MODERATION_PROVIDER=%s，按拒绝处理", provider)
    return ModerationResult(passed=False, reason="审核配置异常，请联系管理员", provider=provider)
