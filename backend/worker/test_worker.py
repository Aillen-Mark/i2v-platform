"""Regression checks for the V2 payload and worker retry decisions."""

import json
from unittest.mock import Mock

import httpx
import pytest

from worker.minimax import GenerationError, MiniMax


def test_v2_first_frame_then_nested_result():
    def upstream(request):
        assert request.headers["authorization"] == "Bearer test-key"
        if request.method == "POST":
            assert request.url.path == "/v2/video_generation"
            assert json.loads(request.content) == {
                "model": "MiniMax-H3-Highspeed",
                "resolution": "768P",
                "duration": 15,
                "ratio": "adaptive",
                "generate_audio": True,
                "content": [
                    {"type": "text", "text": "测试提示词"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,YQ=="},
                        "role": "first_frame",
                    },
                ],
            }
            return httpx.Response(200, json={"task_id": "remote-1"})
        assert request.url.path == "/v2/query/video_generation/remote-1"
        return httpx.Response(
            200, json={"task": {"status": "succeeded", "content": {"url": "https://example.com/video"}}}
        )

    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        api = MiniMax(client, "test-key", "https://example.com")
        task_id = api.create(
            "MiniMax-H3-Highspeed", "768P", 15, "data:image/jpeg;base64,YQ==", "测试提示词", 1
        )
        assert api.result_url(api.query(task_id, 1)) == "https://example.com/video"


@pytest.mark.parametrize(
    ("failure", "code", "retry"),
    [
        (httpx.ReadTimeout("lost response"), "UPSTREAM_ERROR", None),
        (httpx.ConnectTimeout("connection timed out"), "UPSTREAM_ERROR", "resubmit"),
        (httpx.Response(429), "UPSTREAM_ERROR", "resubmit"),
        (httpx.Response(422), "MODERATION_REJECTED", None),
    ],
)
def test_submit_error_does_not_repeat_ambiguous_or_moderated_post(failure, code, retry):
    client = Mock()
    if isinstance(failure, Exception):
        client.request.side_effect = failure
    else:
        client.request.return_value = failure
    api = MiniMax(client, "test-key", "https://example.com")
    with pytest.raises(GenerationError) as raised:
        api.create("MiniMax-H3-Lite", "480P", 4, "image", "prompt", 1)
    assert (raised.value.code, raised.value.retry) == (code, retry)


def test_query_moderation_is_terminal():
    with pytest.raises(GenerationError) as raised:
        MiniMax.result_url({"status": "failed", "error": {"code": "1026"}})
    assert (raised.value.code, raised.value.retry) == ("MODERATION_REJECTED", None)
