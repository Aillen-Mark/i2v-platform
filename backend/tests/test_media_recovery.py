"""Real media processes exercised across durable worker recovery boundaries."""

import asyncio
import ctypes
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from shutil import copyfileobj
from threading import Event, Thread
from time import monotonic, time

import pytest
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models import Quota, Work
from tests.test_generation_recovery import await_takeover, submit, terminate_at_checkpoint
from worker import artifacts, execution, pipeline
from worker.attempts import change_attempt, prepare_attempt
from worker.config import worker_settings
from worker.media_workspace import ORPHAN_GRACE, cleanup_orphans, workspace
from worker.state import load_job
from worker.test_media import media_tools as media_tools, sample_factory as sample_factory


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    root = tmp_path / "scratch"
    monkeypatch.setenv("I2V_MEDIA_TMP_DIR", str(root))
    return root


@pytest.fixture
def provider(sample_factory, request):
    sample = sample_factory(dimensions="1280x720", audio=False, length=getattr(request, "param", 15))
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_POST(self):
            calls.append("POST")
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["duration"] == 15 and request["resolution"] == "768P"
            assert request["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            body = b'{"task_id":"phase3-task"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append(self.path)
            if self.path == "/video.mp4":
                self.send_response(200)
                self.send_header("Content-Length", str(sample.stat().st_size))
                self.end_headers()
                with sample.open("rb") as stream:
                    copyfileobj(stream, self.wfile)
                return
            if self.path.endswith("/phase3-task"):
                body = json.dumps(
                    {
                        "task": {
                            "id": "phase3-task",
                            "status": "succeeded",
                            "content": {"url": f"http://127.0.0.1:{self.server.server_port}/video.mp4"},
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", sample, calls
        finally:
            server.shutdown()
            thread.join(timeout=2)


def known_task(lease):
    attempt = prepare_attempt(lease.work_id, lease=lease)
    submitting = change_attempt(attempt, "submitting", lease=lease)
    return change_attempt(submitting, "accepted", lease=lease, task_id="phase3-task")


def use_provider(patch, address):
    patch.setenv("MINIMAX_BASE_URL", address)
    patch.setenv("MINIMAX_API_KEY", "local-fixture")
    patch.setenv("NO_PROXY", "127.0.0.1,localhost")
    worker_settings.cache_clear()


TRANSCODE_CHILD = """
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
import os
import sys
from worker import media, media_process
from worker.attempts import change_attempt, prepare_attempt
from worker.execution import acquire
from worker.media_workspace import workspace

work_id = int(sys.argv[1])
marker = Path(sys.argv[3])
source = Path(os.environ["I2V_TEST_INPUT"])
lease = acquire(work_id, from_outbox=False, max_seconds=5)
attempt = prepare_attempt(work_id, lease=lease)
assert lease.generation == 2 and attempt.provider_task_id == "phase3-task"
attempt = change_attempt(attempt, "uploading", lease=lease)
original_popen = media_process.subprocess.Popen

with workspace(lease, attempt.id) as directory:
    output = directory / "output.mp4"
    original = media.run_media

    def run(args, stage, deadline, lost, **kwargs):
        if stage == "TRANSCODE":
            args.insert(args.index("-i"), "-re")

            def capture(*a, **kw):
                process = original_popen(*a, **kw)
                def observe():
                    while process.poll() is None:
                        if output.exists():
                            marker.write_text(f"{process.pid}\\n{directory}", encoding="utf-8")
                            return
                        sleep(0.05)
                Thread(target=observe, daemon=True).start()
                return process
            media_process.subprocess.Popen = capture
        return original(args, stage, deadline, lost, **kwargs)

    media.run_media = run
    media.transcode(source, output, 15, monotonic() + 45, Event(), resolution="768P")
"""


def is_process_live(pid):
    if os.name == "nt":
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        process = api.OpenProcess(0x00100000, False, pid)
        if not process:
            return False
        try:
            return api.WaitForSingleObject(process, 0) == 258
        finally:
            api.CloseHandle(process)
    stat = os.path.join("/proc", str(pid), "stat")
    try:
        with open(stat, encoding="utf-8") as stream:
            return stream.read().rsplit(")", 1)[1].strip()[0] != "Z"
    except FileNotFoundError:
        return False


async def test_killed_transcode_is_recovered_without_new_provider_request(
    admin_client,
    candidate_client_factory,
    monkeypatch,
    tmp_path,
    provider,
    scratch,
):
    from pathlib import Path

    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    first = execution.acquire(work_id, from_outbox=True, max_seconds=90)
    known_task(first)
    execution.release(first)
    address, source, calls = provider
    marker = tmp_path / "transcoding"
    await asyncio.to_thread(
        terminate_at_checkpoint,
        work_id,
        "transcoding",
        marker,
        address,
        script=TRANSCODE_CHILD,
        extra_env={"I2V_TEST_INPUT": str(source)},
    )
    pid_text, path_text = marker.read_text(encoding="utf-8").splitlines()
    orphan = Path(path_text)
    wait_until = monotonic() + 5
    while is_process_live(int(pid_text)) and monotonic() < wait_until:
        await asyncio.sleep(0.1)
    assert not is_process_live(int(pid_text))
    assert orphan.is_dir()

    lease = await asyncio.to_thread(await_takeover, work_id)
    assert lease.generation == 3
    attempt = prepare_attempt(work_id, lease=lease)
    assert attempt.status == "uploading" and attempt.provider_task_id == "phase3-task"

    old = time() - ORPHAN_GRACE - 10
    use_provider(monkeypatch, address)
    try:
        with workspace(lease, attempt.id) as current:
            os.utime(orphan, (old, old))
            os.utime(current, (old, old))
            await asyncio.to_thread(cleanup_orphans)
            assert current.is_dir() and not orphan.exists()
        await asyncio.to_thread(pipeline.generate, load_job(work_id), 15, "16:9", lease=lease, lost=Event())
    finally:
        worker_settings.cache_clear()
    assert calls == ["/v2/query/video_generation/phase3-task", "/video.mp4"]
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "succeeded"
        assert session.get(Work, work_id).video_key.endswith("-3.mp4")
        assert session.scalar(select(Quota.quota_used)) == 1
    assert not list(scratch.iterdir())


async def test_partial_upload_preserves_work_and_streams_file(
    admin_client,
    candidate_client_factory,
    monkeypatch,
    provider,
    scratch,
):
    import boto3
    from botocore.config import Config
    from app.core import storage

    class DropUpload(BaseHTTPRequestHandler):
        def do_PUT(self):
            self.rfile.read(512)
            drop_seen.set()
            self.close_connection = True

        def log_message(self, *_):
            return

    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    lease = execution.acquire(work_id, from_outbox=True, max_seconds=90)
    known_task(lease)
    address, _, calls = provider
    original_upload = artifacts.upload_file
    drop_seen = Event()
    streams = []

    def inspect_upload(bucket, key, stream, size, content_type):
        assert not isinstance(stream, bytes)
        assert os.fstat(stream.fileno()).st_size == size > 512
        streams.append(size)
        original_upload(bucket, key, stream, size, content_type)

    use_provider(monkeypatch, address)
    monkeypatch.setattr(artifacts, "upload_file", inspect_upload)
    try:
        with ThreadingHTTPServer(("127.0.0.1", 0), DropUpload) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                incomplete = boto3.client(
                    "s3",
                    endpoint_url=f"http://127.0.0.1:{server.server_port}",
                    aws_access_key_id="fixture",
                    aws_secret_access_key="fixture",
                    region_name="us-east-1",
                    config=Config(retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=2),
                )
                with monkeypatch.context() as patch:
                    patch.setattr(storage, "_CLIENT", incomplete)
                    await asyncio.to_thread(
                        pipeline.generate,
                        load_job(work_id),
                        15,
                        "16:9",
                        lease=lease,
                        lost=Event(),
                    )
            finally:
                server.shutdown()
                thread.join(timeout=2)
        assert drop_seen.is_set()
        with SyncSessionLocal() as session:
            assert session.get(Work, work_id).task_status == "running"
            assert session.get(Work, work_id).video_key is None
            assert session.get(Work, work_id).quota_refunded == 0

        await asyncio.to_thread(pipeline.generate, load_job(work_id), 15, "16:9", lease=lease, lost=Event())
    finally:
        worker_settings.cache_clear()
    assert "POST" not in calls and len(streams) == 2
    with SyncSessionLocal() as session:
        assert session.get(Work, work_id).task_status == "succeeded"
        assert session.scalar(select(Quota.quota_used)) == 1
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("provider", [1], indirect=True)
async def test_unplayable_legacy_checkpoint_cannot_succeed_or_refund(
    admin_client,
    candidate_client_factory,
    monkeypatch,
    provider,
    scratch,
    tmp_path,
    sample_factory,
):
    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    lease = execution.acquire(work_id, from_outbox=True, max_seconds=90)
    attempt = known_task(lease)
    change_attempt(attempt, "uploading", lease=lease)
    from worker.media import transcode

    previous_bytes = tmp_path / "phase2-but-bad.mp4"
    transcode(sample_factory(dimensions="1280x720"), previous_bytes, 15, monotonic() + 45)
    with previous_bytes.open("r+b") as stream:
        stream.truncate(previous_bytes.stat().st_size * 2 // 3)
    artifacts.upload(
        lease, "videos/old-phase2-" + previous_bytes.parent.name + ".mp4", previous_bytes, Event()
    )
    address, _, calls = provider
    use_provider(monkeypatch, address)

    try:
        await asyncio.to_thread(pipeline.generate, load_job(work_id), 15, "16:9", lease=lease, lost=Event())
    finally:
        worker_settings.cache_clear()
    attempt = prepare_attempt(work_id, lease=lease)
    assert attempt.status == "manual_review" and attempt.provider_task_id == "phase3-task"
    assert calls == ["/v2/query/video_generation/phase3-task", "/video.mp4"]
    with SyncSessionLocal() as session:
        work = session.get(Work, work_id)
        assert work.task_status == "running"
        assert work.video_key is None and work.quota_refunded == 0
        assert session.scalar(select(Quota.quota_used)) == 1
    assert not list(scratch.iterdir())


async def test_new_work_runs_real_first_frame_and_media_pipeline(
    admin_client,
    candidate_client_factory,
    monkeypatch,
    provider,
    scratch,
    tmp_path,
    media_tools,
):
    from subprocess import run

    from app.core.config import get_settings
    from app.core.storage import upload_file

    work_id = await submit(admin_client, candidate_client_factory, monkeypatch)
    address, sample, calls = provider
    image = tmp_path / "first.png"
    run(
        [media_tools.I2V_FFMPEG, "-nostdin", "-v", "error", "-i", str(sample), "-frames:v", "1", str(image)],
        check=True,
        timeout=15,
    )
    job = load_job(work_id)
    with image.open("rb") as stream:
        upload_file(
            get_settings().MINIO_BUCKET_PUBLIC, job.work.image_key, stream, image.stat().st_size, "image/png"
        )
    lease = execution.acquire(work_id, from_outbox=True, max_seconds=90)
    use_provider(monkeypatch, address)
    try:
        await asyncio.to_thread(pipeline.generate, job, 15, "16:9", lease=lease, lost=Event())
    finally:
        worker_settings.cache_clear()
    assert calls == ["POST", "/v2/query/video_generation/phase3-task", "/video.mp4"]
    with SyncSessionLocal() as session:
        work = session.get(Work, work_id)
        assert work.task_status == "succeeded" and work.file_size > 0 and work.duration_sec == 15
        assert session.scalar(select(Quota.quota_used)) == 1
    assert not list(scratch.iterdir())
