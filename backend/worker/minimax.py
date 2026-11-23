"""MiniMax H3 V2 requests and stable worker errors."""

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx

RetryMode = Literal["resubmit", "resume"]

MESSAGES = {
    "UPSTREAM_ERROR": "视频生成服务暂时不可用，本次机会已退还，请稍后再试",
    "UPSTREAM_TIMEOUT": "视频生成超时，本次机会已退还，请重新提交",
    "MODERATION_REJECTED": "图片或提示词未通过生成审核，本次机会已退还",
    "INVALID_IMAGE": "首帧图片无法处理，本次机会已退还，请更换图片后重试",
    "MEDIA_FAILED": "成片处理失败，本次机会已退还，请稍后再试",
}


class GenerationError(Exception):
    def __init__(self, code: str = "UPSTREAM_ERROR", retry: RetryMode | None = None):
        super().__init__(code)
        self.code = code
        self.retry = retry


class MiniMax:
    def __init__(self, client: httpx.Client, key: str, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}"}

    def _request(self, method: str, path: str, timeout: float, **kwargs: Any) -> dict:
        creating = method == "POST"
        try:
            response = self.client.request(
                method, f"{self.base_url}{path}", headers=self.headers, timeout=timeout, **kwargs
            )
        except (httpx.ConnectTimeout, httpx.ConnectError):
            raise GenerationError(retry="resubmit" if creating else "resume") from None
        except httpx.RequestError:
            # After an interrupted POST the provider may already have created a paid task.
            raise GenerationError(retry=None if creating else "resume") from None

        if response.is_error:
            if response.status_code == 422:
                raise GenerationError("MODERATION_REJECTED")
            if response.status_code in {429, 529}:
                raise GenerationError(retry="resubmit" if creating else "resume")
            if not creating and response.status_code >= 500:
                raise GenerationError(retry="resume")
            raise GenerationError()

        try:
            payload = response.json()
        except ValueError:
            raise GenerationError(retry=None if creating else "resume") from None
        if not isinstance(payload, dict):
            raise GenerationError()
        return payload

    def create(
        self, model: str, resolution: str, duration: int, image: str, prompt: str, timeout: float
    ) -> str:
        payload = self._request(
            "POST",
            "/v2/video_generation",
            timeout,
            json={
                "model": model,
                "resolution": resolution,
                "duration": duration,
                "ratio": "adaptive",
                "generate_audio": True,
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image}, "role": "first_frame"},
                ],
            },
        )
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            raise GenerationError()
        return task_id

    def query(self, task_id: str, timeout: float) -> dict:
        payload = self._request("GET", f"/v2/query/video_generation/{quote(task_id, safe='')}", timeout)
        task = payload.get("task", payload)
        if not isinstance(task, dict):
            raise GenerationError(retry="resume")
        return task

    def cancel_queued(self, task_id: str, timeout: float) -> None:
        self._request("DELETE", f"/v2/video_generation/{quote(task_id, safe='')}", timeout)

    @staticmethod
    def result_url(task: Mapping) -> str | None:
        status = task.get("status")
        if status == "succeeded":
            content = task.get("content")
            url = content.get("url") if isinstance(content, Mapping) else None
            if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
                raise GenerationError(retry="resume")
            return url
        if status == "failed":
            error = task.get("error")
            if isinstance(error, Mapping) and str(error.get("code")) == "1026":
                raise GenerationError("MODERATION_REJECTED")
            raise GenerationError(retry="resubmit")
        if status == "cancelled":
            raise GenerationError()
        if status not in {"queued", "running"}:
            raise GenerationError(retry="resume")
        return None
