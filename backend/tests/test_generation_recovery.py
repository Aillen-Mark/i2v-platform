"""Fault injection for persistent claims, upstream submission and object recovery."""

import asyncio
import json
import os
import shlex
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from celery import Celery
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.database import SyncSessionLocal
from app.core.timeutil import utcnow
from app.models import AuditLog, GenerationExecution, GenerationOutbox, Quota, Work
from app.services import handoff_sync, works
from app.services.execution_lease import ExecutionStopped
from tests.test_generation_outbox import advance_delivery, prepare_work, read_broker_message
from worker import artifacts, execution, outbox_dispatcher, pipeline
from worker.attempts import change_attempt, prepare_attempt
from worker.state import load_job, stale_work_ids
from worker.tasks import QUEUE, enqueue_video_generation, reconcile_stale
from worker.test_media import media_tools as media_tools, sample_factory as sample_factory

CHILD = """
from pathlib import Path
from threading import Event
from time import sleep
import os
import sys
from worker import pipeline
from worker.artifacts import upload
from worker.attempts import prepare_attempt, change_attempt
from worker.execution import acquire
from worker.state import load_job

work_id, stage, marker = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
lease = acquire(work_id, from_outbox=True, max_seconds=4)
assert lease is not None

def halt():
    marker.write_text("ready", encoding="utf-8")
    sleep(40)

def image(*args):
    if stage == "before_request":
        halt()
    return "data:image/jpeg;base64,YQ=="

pipeline.prepare_first_frame = image
original = pipeline.change_attempt

def change(attempt, target, **kwargs):
    if target == "accepted" and stage == "after_response":
        halt()
    result = original(attempt, target, **kwargs)
    if target == "accepted" and stage == "after_commit":
        halt()
    return result

pipeline.change_attempt = change
if stage == "uploading":
    attempt = prepare_attempt(work_id, lease=lease)
    attempt = change_attempt(attempt, "submitting", lease=lease)
    attempt = change_attempt(attempt, "accepted", lease=lease, task_id="fixture-artifact")
    change_attempt(attempt, "uploading", lease=lease)
    upload(lease, "videos/phase2-upload-" + marker.parent.name + ".mp4", Path(os.environ["I2V_TEST_VALID_MP4"]), Event())
    halt()
else:
    pipeline.generate(load_job(work_id), 15, "16:9", lease=lease, lost=Event())
"""


@pytest.fixture
def provider_server(monkeypatch):
    calls = []
    block = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionResetError, BrokenPipeError):
                pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append("create")
            if block.is_set():
                # Imitate acceptance with no response yet.
                marker.write_text("ready", encoding="utf-8")
                sleep(10)
            self.reply({"task_id": "fixture-task"})

        def do_GET(self):
            calls.append(self.path)
            self.reply({"task": {"id": "fixture-task", "status": "failed", "error": {"code": "1026"}}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    marker = None

    def configure(path, *, hold_response):
        nonlocal marker
        marker = path
        if hold_response:
            block.set()

    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield address, calls, configure
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def terminate_at_checkpoint(work_id, stage, marker, address, *, script=CHILD, extra_env=None):
    environment = os.environ | {
        "MINIMAX_BASE_URL": address,
        "MINIMAX_API_KEY": "local-fixture",
        "REDIS_DB": "15",
        "MYSQL_DATABASE": "i2v_platform_test",
        "PYTHONIOENCODING": "utf-8",
    }
    environment.update(extra_env or {})
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(work_id), stage, str(marker)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    deadline = monotonic() + 12
    try:
        while not marker.exists() and proc.poll() is None and monotonic() < deadline:
            sleep(0.05)
        if marker.exists() and proc.poll() is None:
            proc.kill()
        else:
            proc.kill()
            out, err = proc.communicate(timeout=5)
            pytest.fail(f"Child failed to reach {stage}: {out}\n{err}")
        proc.communicate(timeout=5)
        assert proc.returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def await_takeover(work_id):
    deadline = monotonic() + 7
    while monotonic() < deadline:
        if work_id in stale_work_ids():
            lease = execution.acquire(work_id, from_outbox=False, max_seconds=60)
            if lease is not None:
                return lease
        sleep(0.1)
    pytest.fail("Database lease did not expire or become recoverable")


async def submit(admin_client, candidate_client_factory, monkeypatch):
    client, draft = await prepare_work(admin_client, candidate_client_factory)
    monkeypatch.setattr(works.settings, "MOCK_WORKER", False)
    assert (await client.post(f"/api/works/{draft['id']}/generate")).status_code == 200
    return draft["id"]


@pytest.mark.parametrize(
    ("stage", "calls_before", "calls_after", "final"),
    [
        ("before_request", 0, 1, "failed"),
        ("awaiting_response", 1, 1, "running"),
        ("after_response", 1, 1, "running"),
        ("after_commit", 1, 1, "failed"),
        ("uploading", 0, 0, "succeeded"),
    ],
)
async def test_force_kill_and_takeover(
    stage,
    calls_before,
    calls_after,
    final,
    admin_client,
    candidate_client_factory,
    monkeypatch,
    tmp_path,
    provider_server,
    request,
):
    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    address, calls, configure = provider_server
    marker = tmp_path / "checkpoint"
    configure(marker, hold_response=stage == "awaiting_response")
    child_env = None
    if stage == "uploading":
        factory = request.getfixturevalue("sample_factory")
        from worker.media import transcode

        path = transcode(factory(dimensions="1280x720"), tmp_path / "validated.mp4", 15, monotonic() + 60)
        child_env = {"I2V_TEST_VALID_MP4": str(path)}
    await asyncio.to_thread(terminate_at_checkpoint, work_id, stage, marker, address, extra_env=child_env)
    assert calls.count("create") == calls_before
    if stage == "uploading":
        with SyncSessionLocal.begin() as session:
            row = session.get(GenerationExecution, work_id)
            row.started_at -= timedelta(seconds=45)
    lease = await asyncio.to_thread(await_takeover, work_id)
    assert lease.generation == 2
    from worker.config import worker_settings

    monkeypatch.setenv("MINIMAX_BASE_URL", address)
    monkeypatch.setenv("MINIMAX_API_KEY", "local-fixture")
    worker_settings.cache_clear()
    monkeypatch.setattr(pipeline, "prepare_first_frame", lambda *_: "data:image/jpeg;base64,YQ==")
    try:
        await asyncio.to_thread(pipeline.generate, load_job(work_id), 15, "16:9", lease=lease, lost=Event())
    finally:
        worker_settings.cache_clear()
    assert calls.count("create") == calls_after
    if stage == "after_commit":
        assert any(call.endswith("fixture-task") for call in calls if call != "create")
    with SyncSessionLocal() as session:
        work = session.get(Work, work_id)
        assert work.task_status == final
        if stage == "uploading":
            assert work.cost_seconds >= 45
        assert session.scalar(select(Quota.quota_used)) == (0 if final == "failed" else 1)
        assert session.get(GenerationOutbox, work_id).received_at is not None
    if final == "running":
        attempt = prepare_attempt(work_id, lease=lease)
        assert attempt.status == "submission_unknown"
        assert attempt.provider_task_id is None
        assert await asyncio.to_thread(execution.acquire, work_id, from_outbox=True, max_seconds=60) is None
    if final == "succeeded":
        handoff_sync.mark_work_failed_and_refund(work_id, "UPSTREAM_ERROR", "late duplicate")
        assert execution.acquire(work_id, from_outbox=True, max_seconds=60) is None


async def test_concurrent_claim_heartbeat_and_stale_writes(
    admin_client, candidate_client_factory, monkeypatch, tmp_path
):
    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    assert not execution.reserve_recovery(work_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(
            pool.map(lambda _: execution.acquire(work_id, from_outbox=True, max_seconds=90), range(4))
        )
    (old,) = [lease for lease in leases if lease]
    attempt = prepare_attempt(work_id, lease=old)
    execution.renew(old)
    with SyncSessionLocal.begin() as session:
        row = session.get(GenerationExecution, work_id)
        assert row.last_heartbeat_at is not None
        session.execute(
            update(GenerationExecution)
            .where(GenerationExecution.work_id == work_id)
            .values(
                recover_after=utcnow() - timedelta(seconds=1),
                lease_expires_at=utcnow() - timedelta(seconds=1),
            )
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: execution.reserve_recovery(work_id), range(4))).count(True) == 1
    new = execution.acquire(work_id, from_outbox=False, max_seconds=60)
    assert new.generation == old.generation + 1
    with pytest.raises(ExecutionStopped):
        execution.renew(old)
    with pytest.raises(ExecutionStopped):
        change_attempt(attempt, "submitting", lease=old)
    stale_path = tmp_path / "old.mp4"
    stale_path.write_bytes(b"old bytes")
    with pytest.raises(ExecutionStopped):
        artifacts.upload(old, "videos/phase2-stale.mp4", stale_path, Event())
    with pytest.raises(ExecutionStopped):
        handoff_sync.mark_work_succeeded(work_id, "videos/old.mp4", 3, 15, 1, "old", lease=old)
    failed = change_attempt(attempt, "failed", error="INVALID_IMAGE", lease=new)
    assert failed.status == "failed"
    with pytest.raises(ExecutionStopped):
        handoff_sync.mark_work_failed_and_refund(work_id, "UPSTREAM_ERROR", "stale failure", lease=old)
    for _ in range(2):
        handoff_sync.mark_work_failed_and_refund(work_id, "INVALID_IMAGE", "fixture", lease=new)
    with SyncSessionLocal() as session:
        assert session.scalar(select(Quota.quota_used)) == 0
        assert (
            session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "refund_quota")
            )
            == 1
        )


async def test_celery_entry_renews_lease_and_stops_when_redis_slot_disappears(
    admin_client, candidate_client_factory, monkeypatch, caplog
):
    from worker import state, tasks
    from worker.config import worker_settings

    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    monkeypatch.setattr(worker_settings(), "I2V_HEARTBEAT_SECONDS", 0.1)
    ran = Event()

    def probe(job, duration, ratio, *, lease, lost):
        engine_slots = f"{state.GLOBAL_SLOTS}:{job.work.engine_code}"
        (token,) = state.sync_redis.zrange(engine_slots, 0, -1)
        score = state.sync_redis.zscore(engine_slots, token)
        with SyncSessionLocal() as session:
            started = session.get(GenerationExecution, work_id).last_heartbeat_at
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with SyncSessionLocal() as session:
                heartbeat_at = session.get(GenerationExecution, work_id).last_heartbeat_at
            if heartbeat_at > started and state.sync_redis.zscore(engine_slots, token) > score:
                break
            sleep(0.02)
        else:
            pytest.fail("Heartbeat did not renew the database lease and Redis slot")
        state.sync_redis.zrem(engine_slots, token)
        assert lost.wait(timeout=3)
        with pytest.raises(ExecutionStopped):
            pipeline.require_heartbeat(lease, lost)
        ran.set()

    monkeypatch.setattr(tasks, "generate", probe)
    await asyncio.to_thread(enqueue_video_generation.run, work_id, from_outbox=True)
    assert ran.is_set()
    assert "execution_heartbeat_lost" in caplog.text
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "queued"
        assert session.get(GenerationOutbox, work_id).received_at is not None
        assert session.scalar(select(Quota.quota_used)) == 1


@pytest.mark.parametrize("provider_task_id", ["legacy-provider-task", None, "mock-legacy"])
async def test_legacy_received_work_preserves_known_ids_and_isolates_unknown(
    provider_task_id, admin_client, candidate_client_factory, monkeypatch
):
    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    with SyncSessionLocal.begin() as session:
        work = session.get(Work, work_id)
        work.upstream_task_id = provider_task_id
        work.task_status = "running"
        work.updated_at = utcnow() - timedelta(minutes=5)
        session.get(GenerationOutbox, work_id).received_at = work.updated_at

    assert work_id in stale_work_ids()
    assert execution.reserve_recovery(work_id)
    lease = execution.acquire(work_id, from_outbox=False, max_seconds=90)
    assert lease and lease.generation == 1
    attempt = prepare_attempt(work_id, allow_new=lease.allow_new, lease=lease)
    if provider_task_id == "legacy-provider-task":
        assert attempt.status == "accepted"
        assert attempt.provider_task_id == provider_task_id
    else:
        assert attempt.status == "submission_unknown"
        assert attempt.provider_task_id is None
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).upstream_task_id == provider_task_id
        assert session.scalar(select(Quota.quota_used)) == 1


DISPATCH_CHILD = """
import sys
from pathlib import Path
from time import sleep
from worker import outbox_dispatcher

def pause(delivery, *, error=None):
    assert not error
    Path(sys.argv[3]).write_text(str(delivery.work_id), encoding="utf-8")
    sleep(40)

outbox_dispatcher.finish_delivery = pause
outbox_dispatcher.dispatch_once()
"""

API_CHILD = """
import asyncio
import os
import sys
from pathlib import Path
from time import sleep
import httpx
from app.main import app

async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(f"/api/works/{sys.argv[1]}/generate", cookies={"cand_sid":os.environ["TEST_SID"]})
        assert response.status_code == 200, response.text

asyncio.run(run())
Path(sys.argv[3]).write_text("committed", encoding="utf-8")
sleep(40)
"""


async def test_api_and_dispatcher_killed_at_commit_boundaries(
    admin_client, candidate_client_factory, tmp_path
):
    client, draft = await prepare_work(admin_client, candidate_client_factory)
    work_id = draft["id"]
    await asyncio.to_thread(
        terminate_at_checkpoint,
        work_id,
        "api",
        tmp_path / "api",
        "",
        script=API_CHILD,
        extra_env={"TEST_SID": client.cookies["cand_sid"], "MOCK_WORKER": "false"},
    )
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "queued"
        assert session.get(GenerationOutbox, work_id).attempts == 0
        assert session.scalar(select(Quota.quota_used)) == 1
    await asyncio.to_thread(
        terminate_at_checkpoint,
        work_id,
        "dispatcher",
        tmp_path / "dispatcher",
        "",
        script=DISPATCH_CHILD,
    )
    assert (await asyncio.to_thread(read_broker_message))[1]["work_id"] == work_id
    with SyncSessionLocal() as session:
        event = session.get(GenerationOutbox, work_id)
        assert event.published_at is None and event.lease_token
    await asyncio.to_thread(advance_delivery, work_id)
    assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
    assert (await asyncio.to_thread(read_broker_message))[1]["work_id"] == work_id
    assert execution.acquire(work_id, from_outbox=True, max_seconds=90)
    assert execution.acquire(work_id, from_outbox=True, max_seconds=90) is None


async def test_database_unavailable_cannot_mark_uploaded_work_successful(
    admin_client, candidate_client_factory, monkeypatch, sample_factory, tmp_path
):
    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    lease = execution.acquire(work_id, from_outbox=True, max_seconds=90)
    attempt = prepare_attempt(work_id, lease=lease)
    attempt = change_attempt(attempt, "submitting", lease=lease)
    attempt = change_attempt(attempt, "accepted", lease=lease, task_id="db-failure-fixture")
    change_attempt(attempt, "uploading", lease=lease)
    from worker.media import transcode

    path = transcode(sample_factory(dimensions="1280x720"), tmp_path / "validated.mp4", 15, monotonic() + 60)
    artifact = artifacts.upload(lease, f"videos/phase2-db-{uuid4().hex}.mp4", path, Event())
    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        failed_engine = create_engine(
            f"mysql+pymysql://127.0.0.1:{unserved.getsockname()[1]}/i2v_platform_test",
            connect_args={"connect_timeout": 1},
        )
        with monkeypatch.context() as patch:
            patch.setattr(handoff_sync, "SyncSessionLocal", sessionmaker(failed_engine))
            with pytest.raises(OperationalError):
                handoff_sync.mark_work_succeeded(
                    work_id, artifact.key, artifact.size, 15, 1, "db-failure-fixture", lease=lease
                )
        failed_engine.dispose()
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "running"
    assert (
        artifacts.recover(lease, tmp_path / "restored.mp4", 15, "768P", monotonic() + 30, Event()) == artifact
    )
    handoff_sync.mark_work_succeeded(
        work_id, artifact.key, artifact.size, 15, 1, "db-failure-fixture", lease=lease
    )
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "succeeded"


async def test_redis_restart_replays_pending_outbox_and_recovers_received_work(
    admin_client,
    candidate_client_factory,
    monkeypatch,
):
    configured = os.getenv("I2V_TEST_REDIS_DOCKER")
    if not configured:
        pytest.skip("Set I2V_TEST_REDIS_DOCKER to an isolated Docker command to test a real Redis restart")
    docker = shlex.split(configured)
    name = "i2v-phase2-test-" + uuid4().hex[:10]

    def engine(*args):
        result = subprocess.run([*docker, *args], capture_output=True, check=True, timeout=25)
        return result.stdout.decode("utf-8", errors="ignore").replace("\x00", "").strip()

    engine(
        "run",
        "-d",
        "--name",
        name,
        "-p",
        "127.0.0.1::6379",
        "redis:7-alpine",
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "no",
    )

    def published_url():
        port = int(engine("port", name, "6379/tcp").splitlines()[-1].rsplit(":", 1)[-1])
        return f"redis://127.0.0.1:{port}/0"

    def wait_for_queue_size(url, size):
        with Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) as ephemeral:
            for _ in range(30):
                try:
                    if ephemeral.llen(QUEUE) == size:
                        return
                except RedisError:
                    pass
                sleep(0.2)
        pytest.fail(f"Ephemeral Redis did not produce queue size {size}")

    def use_broker(url):
        broker = Celery("phase2-restart", broker=url)
        broker.conf.broker_transport_options = {"max_retries": 0, "socket_connect_timeout": 1}

        def publish(**options):
            with broker.connection_for_write() as connection:
                return broker.send_task("i2v.generate_video", queue=QUEUE, connection=connection, **options)

        monkeypatch.setattr(enqueue_video_generation, "apply_async", publish)

    try:
        broker_url = published_url()
        wait_for_queue_size(broker_url, 0)
        work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
        use_broker(broker_url)
        assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
        wait_for_queue_size(broker_url, 1)

        # A dynamically allocated Docker host port can change across restarts.
        engine("restart", name)
        broker_url = published_url()
        wait_for_queue_size(broker_url, 0)
        use_broker(broker_url)
        await asyncio.to_thread(advance_delivery, work_id)
        assert await asyncio.to_thread(outbox_dispatcher.dispatch_once)
        wait_for_queue_size(broker_url, 1)
        lease = execution.acquire(work_id, from_outbox=True, max_seconds=90)
        attempt = prepare_attempt(work_id, lease=lease)
        attempt = change_attempt(attempt, "submitting", lease=lease)
        change_attempt(attempt, "accepted", lease=lease, task_id="restart-fixture")
        execution.release(lease)
        with SyncSessionLocal.begin() as session:
            session.get(GenerationExecution, work_id).recover_after = utcnow() - timedelta(seconds=1)

        engine("restart", name)
        broker_url = published_url()
        wait_for_queue_size(broker_url, 0)
        use_broker(broker_url)
        await asyncio.to_thread(reconcile_stale)
        await asyncio.to_thread(reconcile_stale)
        wait_for_queue_size(broker_url, 1)
        new = execution.acquire(work_id, from_outbox=False, max_seconds=90)
        assert new and new.generation == 2
        assert prepare_attempt(work_id, lease=new).provider_task_id == "restart-fixture"
        assert execution.acquire(work_id, from_outbox=True, max_seconds=90) is None
    finally:
        engine("rm", "-f", name)
