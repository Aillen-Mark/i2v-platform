"""MiniMax H3 V2 requests and stable worker errors."""

import re
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
    def __init__(
        self, code: str = "UPSTREAM_ERROR", retry: RetryMode | None = None, *, uncertain: bool = False
    ):
        super().__init__(code)
        self.code = code
        self.retry = retry
        self.uncertain = uncertain
        self.timed_out = False


def valid_task_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


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
        except (httpx.ConnectTimeout, httpx.ConnectError) as error:
            failure = GenerationError(retry="resubmit" if creating else "resume")
            failure.timed_out = isinstance(error, httpx.ConnectTimeout)
            raise failure from None
        except httpx.RequestError as error:
            failure = GenerationError(retry=None if creating else "resume", uncertain=creating)
            if isinstance(error, httpx.TimeoutException):
                failure.timed_out = True
            raise failure from None

        status = response.status_code
        if not 200 <= status < 300:
            if not creating:
                raise GenerationError(retry="resume")
            if status == 422:
                raise GenerationError("MODERATION_REJECTED")
            if status == 429:
                raise GenerationError(retry="resubmit")
            if status in {400, 401, 402, 403}:
                raise GenerationError()
            raise GenerationError(uncertain=True)

        try:
            payload = response.json()
        except ValueError:
            raise GenerationError(retry=None if creating else "resume", uncertain=creating) from None
        if not isinstance(payload, dict):
            raise GenerationError(retry=None if creating else "resume", uncertain=creating)
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
        if not valid_task_id(task_id):
            raise GenerationError(uncertain=True)
        return task_id

    def query(self, task_id: str, timeout: float) -> dict:
        payload = self._request("GET", f"/v2/query/video_generation/{quote(task_id, safe='')}", timeout)
        task = payload.get("task", payload)
        if not isinstance(task, dict) or ("id" in task and task["id"] != task_id):
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
