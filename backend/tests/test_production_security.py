from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet
from redis.connection import parse_url
from sqlalchemy.engine import make_url
from starlette.requests import Request
from starlette.responses import Response

from app.core import config, sessions
from app.core.deps import client_ip
from app.services import handoff, handoff_sync, moderation


def production_settings(**overrides):
    return config.Settings(
        _env_file=None,
        **(
            {
                "APP_ENV": "production",
                "DEBUG": False,
                "SQL_ECHO": False,
                "MOCK_WORKER": False,
                "MODERATION_PROVIDER": "http",
                "MODERATION_ENDPOINT": "https://review.example.test/image",
                "AES_FERNET_KEY": Fernet.generate_key().decode(),
                "MYSQL_PASSWORD": "fixture-db@:/?#",
                "REDIS_PASSWORD": "fixture-redis@:/?#",
                "MINIO_ACCESS_KEY": "fixture-access",
                "MINIO_SECRET_KEY": "fixture-storage",
                "INIT_ADMIN_PASSWORD": "",
                "ADMIN_INIT_PASSWORD": "fixture-admin",
            }
            | overrides
        ),
    )


def test_production_rejects_insecure_settings_without_echoing_values():
    settings = production_settings(
        DEBUG=True,
        SQL_ECHO=True,
        MOCK_WORKER=True,
        MODERATION_PROVIDER="mock",
        MODERATION_ENDPOINT="http://private.example.test/sensitive",
        AES_FERNET_KEY="invalid-test-secret",
        MYSQL_PASSWORD="i2v_pwd_2026",
        REDIS_PASSWORD="",
        MINIO_ACCESS_KEY="minioadmin",
        MINIO_SECRET_KEY="minioadmin123",
        ADMIN_INIT_PASSWORD=config.DEFAULT_ADMIN_PASSWORD,
    )
    with pytest.raises(RuntimeError) as failure:
        settings.validate_production()
    message = str(failure.value)
    for key in (
        "DEBUG",
        "SQL_ECHO",
        "MOCK_WORKER",
        "MODERATION_PROVIDER",
        "MODERATION_ENDPOINT",
        "AES_FERNET_KEY",
        "MYSQL_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "ADMIN_INIT_PASSWORD",
    ):
        assert key in message
    for value in (
        "private.example.test",
        "invalid-test-secret",
        "i2v_pwd_2026",
        "minioadmin",
        config.DEFAULT_ADMIN_PASSWORD,
    ):
        assert value not in message


def test_production_startup_uses_supplied_key_and_keeps_configuration_read_only(tmp_path):
    settings = production_settings()
    settings.validate_production()
    original_key = settings.AES_FERNET_KEY
    encrypted = Fernet(original_key.encode()).encrypt(b"old candidate password")
    env_file = tmp_path / ".env"
    original = b"APP_ENV=production\nAES_FERNET_KEY=invalid-on-disk\n"
    env_file.write_bytes(original)
    env = os.environ | {key: str(value) for key, value in settings.model_dump().items()}
    env["PYTHONPATH"] = str(config.ROOT_DIR / "backend")
    env["PYTHONIOENCODING"] = "utf-8"
    script = (
        "from pathlib import Path; "
        "from app.core import config; "
        "from cryptography.fernet import Fernet; "
        "config.ENV_FILE = Path('.env'); "
        "config.Settings.model_config['env_file'] = '.env'; "
        f"print(Fernet(config.get_settings().AES_FERNET_KEY.encode()).decrypt({encrypted!r}).decode())"
    )
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # The child imports the app from the same source tree as pytest.
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "old candidate password"
    assert env_file.read_bytes() == original

    env.pop("AES_FERNET_KEY")
    failed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert failed.returncode != 0
    assert "AES_FERNET_KEY" in failed.stderr and "invalid-on-disk" not in failed.stderr
    assert env_file.read_bytes() == original


def test_invalid_bootstrap_config_fails_before_database_startup():
    env = os.environ | {
        "APP_ENV": "production",
        "DEBUG": "bad-format-do-not-echo",
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode != 0
    assert "DEBUG" in result.stderr
    assert "bad-format-do-not-echo" not in result.stderr


def test_config_allows_special_characters_in_database_credentials():
    settings = production_settings()
    assert make_url(settings.database_url).password == settings.MYSQL_PASSWORD
    assert make_url(settings.sync_database_url).password == settings.MYSQL_PASSWORD
    assert parse_url(settings.redis_url)["password"] == settings.REDIS_PASSWORD


@pytest.mark.parametrize("production", [True, False])
def test_cookie_security_and_names(monkeypatch, production):
    settings = production_settings(APP_ENV="production" if production else "local")
    monkeypatch.setattr(sessions, "get_settings", lambda: settings)
    for role in ("candidate", "admin"):
        response = Response()
        sessions.set_session_cookie(response, role, "fixture-sid")
        sessions.clear_session_cookie(response, role)
        for header in response.headers.getlist("set-cookie"):
            assert header.startswith(sessions.COOKIE_NAME[role] + "=")
            assert "HttpOnly" in header and "SameSite=lax" in header
            assert ("; Secure" in header) is production


def test_audit_ip_uses_asgi_address():
    request = Request(
        {
            "type": "http",
            "client": ("192.0.2.10", 1234),
            "headers": [(b"x-forwarded-for", b"198.51.100.20")],
        }
    )
    assert client_ip(request) == "192.0.2.10"


async def test_moderation_http_contract_and_failure_logging(monkeypatch, caplog):
    samples = [
        (200, b'{"passed":true}', True),
        (200, b'{"passed":false,"reason":"private-provider-detail"}', False),
        (200, b'{"passed":"false"}', False),
        (200, b'{"passed":true,"reason":{"bad":1}}', False),
        (200, b"[]", False),
        (200, b"private-provider-detail", False),
        (503, b"private-provider-detail", False),
    ]
    settings = production_settings()
    monkeypatch.setattr(moderation, "get_settings", lambda: settings)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    async def serve(reader, writer):
        raw = await reader.readuntil(b"\r\n\r\n")
        for row in raw.splitlines():
            if row.lower().startswith(b"content-length:"):
                body = await reader.readexactly(int(row.split(b":")[1]))
                assert json.loads(body)["data_b64"] == "aW1hZ2U="
        status, body, _ = samples.pop(0)
        writer.write(
            f"HTTP/1.1 {status} reply\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    expected = [expected for _, _, expected in samples]
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    settings.MODERATION_ENDPOINT = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    async with server:
        for passed in expected:
            result = await moderation.moderate_image(b"image", "frame.jpg", "image/jpeg")
            assert result.passed is passed
            assert "private-provider-detail" not in (result.reason or "")
    result = await moderation.moderate_image(b"image", "frame.jpg", "image/jpeg")
    assert not result.passed
    assert not samples
    assert "private-provider-detail" not in caplog.text


async def test_handoff_logs_do_not_emit_signed_urls(monkeypatch, caplog):
    monkeypatch.setattr(handoff.settings, "MOCK_WORKER", False)
    monkeypatch.setattr(handoff_sync.settings, "MOCK_WORKER", False)
    sent = []

    def publisher(*args):
        sent.append(args)

    monkeypatch.setattr(handoff_sync, "_celery_delay", publisher)
    caplog.set_level(logging.INFO)
    link = "https://storage.example.test/input.jpg?X-Amz-Signature=fixture-signature"
    await handoff.trigger_video_generation(8001, link, "fixture private prompt", "wq-v1")
    handoff_sync.trigger_video_generation(8002, link, "fixture private prompt", "wq-v1")
    assert len(sent) == 2
    assert all(args[1] == link for args in sent)
    assert "8001" in caplog.text and "8002" in caplog.text
    assert "fixture-signature" not in caplog.text and "fixture private prompt" not in caplog.text
