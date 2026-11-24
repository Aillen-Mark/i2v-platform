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
    unavailable = ModerationResult(False, "审核服务不可用，请稍后重试", "http")
    try:
        async with httpx.AsyncClient(timeout=settings.MODERATION_TIMEOUT) as client:
            response = await client.post(settings.MODERATION_ENDPOINT, json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("审核服务返回异常状态：%s", exc.response.status_code)
        return unavailable
    except (httpx.RequestError, ValueError):
        logger.warning("审核服务请求失败或响应不是有效 JSON")
        return unavailable

    if not isinstance(body, dict) or not isinstance(body.get("passed"), bool):
        logger.warning("审核服务响应缺少布尔类型的 passed 字段")
        return unavailable
    if body.get("reason") is not None and not isinstance(body["reason"], str):
        logger.warning("审核服务响应的 reason 类型不符")
        return unavailable
    passed = body["passed"]
    return ModerationResult(passed, None if passed else "内容未通过审核", "http")


async def moderate_image(data: bytes, filename: str, content_type: str) -> ModerationResult:
    """审核一张待上传图片；provider 未知时按拒绝处理，避免「配置写错就全放行」。"""
    provider = (get_settings().MODERATION_PROVIDER or "mock").strip().lower()
    if provider == "mock":
        return await _moderate_mock(data, filename, content_type)
    if provider == "http":
        return await _moderate_http(data, filename, content_type)
    logger.error("未知的 MODERATION_PROVIDER=%s，按拒绝处理", provider)
    return ModerationResult(passed=False, reason="审核配置异常，请联系管理员", provider=provider)
