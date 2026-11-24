"""Isolated MySQL and real S3 protocol tests for Issue 008 tooling."""

import csv
import gzip
import json
import os
import shutil
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.storage import ObjectStore, new_s3_client
from app.models import GenerationExecution, Work
from worker.object_backup import backup_objects, fetch_database, restore_objects
from worker.object_reconciliation import reconcile
from worker.object_transfer import TransferBudget, file_digest, read_object, store_verified
from worker.test_media import media_tools as media_tools, sample_factory as sample_factory
from worker.media import transcode
from time import monotonic

PREFIX = "tests/phase4"


@pytest.fixture
def stores(request):
    settings = get_settings()
    primary = new_s3_client(
        settings.minio_endpoint_url,
        settings.MINIO_REGION,
        settings.MINIO_ACCESS_KEY,
        settings.MINIO_SECRET_KEY,
    )
    endpoint = os.getenv("I2V_TEST_BACKUP_ENDPOINT", settings.minio_endpoint_url)
    secondary = new_s3_client(
        endpoint, settings.MINIO_REGION, settings.MINIO_ACCESS_KEY, settings.MINIO_SECRET_KEY
    )
    suffix = uuid4().hex[:12]
    registered = []

    def register(client, name):
        store = ObjectStore(f"i2v-008-{name}-{suffix}", client)
        client.create_bucket(Bucket=store.bucket)
        registered.append(store)
        return store

    def cleanup():
        for store in registered:
            page, token = store.page(500)
            assert token is None
            for row in page:
                store.client.delete_object(Bucket=store.bucket, Key=row.key)
            store.client.delete_bucket(Bucket=store.bucket)

    request.addfinalizer(cleanup)
    return register(primary, "source"), register(secondary, "backup"), register(primary, "restore")


def seed_work(key, size, digest=None, *, status="succeeded", checkpoint=None):
    now = datetime.now(UTC).replace(tzinfo=None)
    with SyncSessionLocal.begin() as session:
        work = Work(
            task_no="TEST-" + uuid4().hex,
            candidate_id=1,
            question_id=1,
            image_key="fixture.png",
            prompt="object fixture",
            task_status=status,
            file_size=size,
            video_key=key if status == "succeeded" else None,
            duration_sec=15,
        )
        session.add(work)
        session.flush()
        if digest or checkpoint:
            session.add(
                GenerationExecution(
                    work_id=work.id,
                    started_at=now,
                    recover_after=now,
                    lease_generation=1,
                    lease_owner="a" * 32 if status == "running" else None,
                    lease_expires_at=now + timedelta(minutes=1) if status == "running" else None,
                    artifact_key=checkpoint or key,
                    artifact_size=size,
                    artifact_sha256=digest,
                )
            )
        return work.id


def digest_bytes(value: bytes):
    from hashlib import sha256

    return sha256(value).hexdigest()


def put(store, key, data):
    store.client.put_object(Bucket=store.bucket, Key=key, Body=data, ContentType="video/mp4")


def run_check(source, path, **options):
    code, report, summary = reconcile(source, path, rate=0, batch_size=2, **options)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"] == summary
    with report.with_suffix(".csv").open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == len(payload["findings"])
    return code, payload, report


def test_inventory_detects_anomalies_and_resumes_without_mutation(stores, tmp_path):
    source, _, _ = stores
    good = b"a known media object"
    seed_work("legacy-good", len(good), digest_bytes(good))
    seed_work("legacy-missing", len(good), digest_bytes(good))
    seed_work("legacy-corrupt", len(good), digest_bytes(good))
    seed_work("legacy-wrong-size", 100, digest_bytes(good))
    seed_work("legacy-no-digest", len(good))
    seed_work("shared", len(good))
    seed_work("shared", len(good))
    seed_work(None, None)
    active = seed_work(None, len(good), status="running", checkpoint="legacy-active")

    for key in ("legacy-good", "legacy-wrong-size", "legacy-no-digest", "shared", "legacy-active"):
        put(source, key, good)
    put(source, "legacy-missing", good)
    source.client.delete_object(Bucket=source.bucket, Key="legacy-missing")
    put(source, "legacy-corrupt", good)
    put(source, "legacy-corrupt", b"X" * len(good))
    put(source, "videos/generated/9876543/W1-1.mp4", good)
    put(source, f"videos/generated/{active}/ACTIVE-1.mp4", good)
    put(source, "videos/mock/unlinked.mp4", good)
    before, _ = source.page(500)

    # Effective now is advanced for the test; S3 timestamps and object bodies are real.
    code, first, report = run_check(source, tmp_path, mode="full", limit=2, grace_days=-1)
    assert code == 1 and first["summary"]["database_records_scanned"] == 2
    cursor = json.loads(report.with_suffix(".cursor.json").read_text(encoding="utf-8"))
    code, second, _ = run_check(source, tmp_path, mode="full", resume=cursor, grace_days=-1)
    assert code == 1
    rows = first["findings"] + second["findings"]
    groups = {row["object_key"]: row["classification"] for row in rows}
    assert groups["legacy-good"] == "HEALTHY"
    assert groups["legacy-missing"] == "MISSING_OBJECT"
    assert groups["legacy-corrupt"] == "CHECKSUM_MISMATCH"
    assert groups["legacy-wrong-size"] == "SIZE_MISMATCH"
    assert groups["legacy-no-digest"] == "LEGACY_UNVERIFIED"
    assert groups["shared"] == "REFERENCE_CONFLICT"
    assert groups["legacy-active"] == "ACTIVE_CHECKPOINT"
    assert groups[f"videos/generated/{active}/ACTIVE-1.mp4"] == "ACTIVE_CHECKPOINT"
    assert groups["videos/generated/9876543/W1-1.mp4"] == "ORPHAN_OBJECT"
    assert groups["videos/mock/unlinked.mp4"] == "UNCLASSIFIED_OBJECT"
    assert first["summary"]["objects_scanned"] + second["summary"]["objects_scanned"] == len(before)
    assert source.page(500)[0] == before
    with SyncSessionLocal() as session:
        statuses = list(session.scalars(select(Work.task_status)).all())
        assert statuses.count("succeeded") == 8 and statuses.count("running") == 1
    _, quick, _ = run_check(source, tmp_path)
    assert quick["summary"]["healthy_count"] == 0 and quick["summary"]["metadata_match_count"] == 2
    assert quick["summary"]["grace_period_count"] == 1


def test_reconciliation_reports_real_s3_and_mysql_connection_failures(stores, tmp_path):
    source, _, _ = stores
    seed_work("need-head", 3)
    settings = get_settings()
    with socket.socket() as unopened:
        unopened.bind(("127.0.0.1", 0))
        port = unopened.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        bad_storage = ObjectStore(source.bucket, new_s3_client(endpoint, "us-east-1", "fixture", "fixture"))
        engine = create_engine(
            f"mysql+pymysql://127.0.0.1:{port}/{settings.MYSQL_DATABASE}", connect_args={"connect_timeout": 1}
        )
        try:
            code, data, _ = run_check(bad_storage, tmp_path)
            assert code == 2 and data["summary"]["storage_error_count"] == 1
            assert not data["summary"]["missing_count"]
            code, data, _ = run_check(source, tmp_path, sessions=sessionmaker(engine))
            assert code == 2 and data["summary"]["database_error_count"] == 1
            assert not data["summary"]["orphan_count"]
        finally:
            engine.dispose()

    missing_bucket = ObjectStore("i2v-008-never-existed-" + uuid4().hex[:8], source.client)
    code, data, _ = run_check(missing_bucket, tmp_path)
    assert code == 2 and data["summary"]["storage_error_count"] == 1


def test_backup_partial_unconfigured_and_conflicting_dedup(stores, tmp_path):
    source, back, target = stores
    ready = b"verified-object-content"
    seed_work("old-key", len(ready), digest_bytes(ready))
    put(source, "old-key", ready)
    seed_work("absent-key", 3)
    result, _ = backup_objects(source, None, tmp_path, local_drill=True)
    assert result["status"] == "OFFSITE_NOT_CONFIGURED"
    result, _ = backup_objects(
        source, back, tmp_path, prefix=PREFIX, local_drill=True, budget=TransferBudget(30, 0)
    )
    assert result["status"] == "PARTIAL" and result["verified_count"] == result["failed_count"] == 1
    assert back.head(f"{PREFIX}/runs/{result['backup_id']}/seal.sha256") is None
    rejected, _ = restore_objects(
        back,
        target,
        PREFIX,
        result["backup_id"],
        tmp_path,
        budget=TransferBudget(10, 0),
        allow_unlinked_drill=True,
    )
    assert rejected["status"] == "FAILED" and not rejected["restored_count"]

    data_key = f"{PREFIX}/data/sha256/{digest_bytes(ready)}"
    put(back, data_key, b"X" * len(ready))
    before = read_object(back, data_key, len(ready), TransferBudget(10, 0))
    result, _ = backup_objects(
        source, back, tmp_path, prefix=PREFIX, local_drill=True, budget=TransferBudget(30, 0)
    )
    assert result["status"] == "FAILED" and not result["inventory_complete"]
    assert read_object(back, data_key, len(ready), TransferBudget(10, 0)) == before
    assert read_object(source, "old-key", len(ready), TransferBudget(10, 0)).sha256 == digest_bytes(ready)


@pytest.fixture
def snapshot(tmp_path):
    root = os.getenv("I2V_TEST_MYSQL_BIN")
    if not root:
        pytest.skip("Set I2V_TEST_MYSQL_BIN for the isolated mysqldump and restore drill")
    bindir = Path(root)
    suffix = ".exe" if os.name == "nt" else ""
    mysql, mysqldump = [str(bindir / (name + suffix)) for name in ("mysql", "mysqldump")]
    if not all(Path(tool).is_file() for tool in (mysql, mysqldump)):
        pytest.skip("MySQL client executables unavailable")
    settings = get_settings()
    assert settings.MYSQL_DATABASE == "i2v_platform_test"
    temporary_db = "i2v_phase4_restore_" + uuid4().hex[:10]
    folder = tmp_path / "mysql-source-snapshot"
    folder.mkdir()
    options = ["--protocol=TCP", "-h", settings.MYSQL_HOST, "-P", str(settings.MYSQL_PORT), "-u", "root"]
    env = dict(os.environ, MYSQL_PWD=settings.MYSQL_ROOT_PASSWORD)
    with (folder / "database.sql.gz").open("xb") as disk:
        with subprocess.Popen(
            [
                mysqldump,
                *options,
                "--single-transaction",
                "--quick",
                "--skip-add-drop-table",
                "--set-gtid-purged=OFF",
                "--no-tablespaces",
                settings.MYSQL_DATABASE,
            ],
            stdout=subprocess.PIPE,
            env=env,
        ) as process:
            with gzip.GzipFile(fileobj=disk, mode="wb") as archive:
                shutil.copyfileobj(process.stdout, archive)
            process.stdout.close()
            assert process.wait(timeout=20) == 0
    (folder / "metadata.txt").write_text(
        f"schema=i2v-mysql-backup-v1\ndatabase={settings.MYSQL_DATABASE}\n"
        f"started_utc={datetime.now(UTC):%Y%m%dT%H%M%SZ}\n",
        encoding="utf-8",
    )
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{file_digest(folder / name).sha256}  {name}\n" for name in ("database.sql.gz", "metadata.txt")
        ),
        encoding="ascii",
    )

    root_connection = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user="root",
        password=settings.MYSQL_ROOT_PASSWORD,
        autocommit=True,
    )
    engine = create_engine(settings.sync_database_url.rsplit("/", 1)[0] + f"/{temporary_db}?charset=utf8mb4")
    try:
        with root_connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{temporary_db}`")
            cursor.execute(f"GRANT SELECT ON `{temporary_db}`.* TO %s@'%%'", (settings.MYSQL_USER,))
        with gzip.open(folder / "database.sql.gz", "rb") as archive:
            subprocess.run(
                [mysql, *options, "--binary-mode", temporary_db],
                input=archive.read(),
                env=env,
                timeout=30,
                check=True,
            )
        yield folder, sessionmaker(engine)
    finally:
        engine.dispose()
        with root_connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{temporary_db}`")
        root_connection.close()


def test_real_backup_restore_and_seals(stores, tmp_path, sample_factory, request):
    source, back, target = stores
    sample = transcode(sample_factory(), tmp_path / "validated.mp4", 15, monotonic() + 45)
    size = sample.stat().st_size
    expected = file_digest(sample)
    seed_work("old/key/作品.mp4", size, expected.sha256)
    assert store_verified(source, "old/key/作品.mp4", sample, expected, TransferBudget(15, 0), "video/mp4")
    folder, restored_db = request.getfixturevalue("snapshot")
    kw = {
        "prefix": PREFIX,
        "snapshot": folder,
        "confirmed": True,
        "local_drill": True,
        "sessions": restored_db,
    }
    result, _ = backup_objects(source, back, tmp_path, budget=TransferBudget(35, 0), **kw)
    assert result["status"] == "COMPLETE" and result["consistency_status"] == "CONSISTENT"
    assert result["destination_kind"] == "LOCAL_DRILL" and result["copied_count"] == 1
    repeated, _ = backup_objects(source, back, tmp_path, budget=TransferBudget(35, 0), **kw)
    assert repeated["status"] == "COMPLETE" and repeated["skipped_count"] == 1
    archive = fetch_database(
        back, PREFIX, result["backup_id"], tmp_path / "downloaded", TransferBudget(20, 0)
    )
    assert file_digest(archive / "database.sql.gz").sha256 == result["database_backup_hash"]
    summary, _ = restore_objects(
        back,
        target,
        PREFIX,
        result["backup_id"],
        tmp_path,
        budget=TransferBudget(40, 0),
        sessions=restored_db,
    )
    assert summary["status"] == "COMPLETE" and summary["media_verified_count"] == 1
    assert read_object(target, "old/key/作品.mp4", size, TransferBudget(10, 0)) == expected
    repeated, _ = restore_objects(
        back,
        target,
        PREFIX,
        result["backup_id"],
        tmp_path,
        budget=TransferBudget(40, 0),
        sessions=restored_db,
    )
    assert repeated["status"] == "COMPLETE" and repeated["already_present_count"] == 1
    conflict = b"other contents"
    put(target, "old/key/作品.mp4", conflict)
    summary, _ = restore_objects(
        back,
        target,
        PREFIX,
        result["backup_id"],
        tmp_path,
        budget=TransferBudget(30, 0),
        sessions=restored_db,
    )
    assert summary["status"] == "FAILED" and summary["error"] == "destination_size_or_sha256_mismatch"
    assert read_object(
        target, "old/key/作品.mp4", len(conflict), TransferBudget(10, 0)
    ).sha256 == digest_bytes(conflict)
    put(back, f"{PREFIX}/runs/{result['backup_id']}/objects.jsonl", b"tampered")
    summary, _ = restore_objects(
        back,
        target,
        PREFIX,
        result["backup_id"],
        tmp_path,
        budget=TransferBudget(30, 0),
        sessions=restored_db,
    )
    assert summary["status"] == "FAILED" and not summary["restored_count"]


def test_conditional_put_enforced_even_when_peer_creates_after_head(stores, tmp_path):
    source, _, _ = stores
    original = b"racing writer"
    local = tmp_path / "body"
    local.write_bytes(b"different payload")
    put(source, "race", original)
    with local.open("rb") as stream:
        assert not source.put_new("race", stream, local.stat().st_size, "video/mp4")
    result = read_object(source, "race", len(original), TransferBudget(15, 0))
    assert result.sha256 == digest_bytes(original)


def test_backup_retries_after_real_s3_connection_drop(stores, tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread

    import boto3
    from botocore.config import Config

    source, back, _ = stores
    contents = b"source survives interruption" * 10000
    put(source, "data.mp4", contents)
    seed_work("data.mp4", len(contents), digest_bytes(contents))
    hit = Event()

    class DropUpload(BaseHTTPRequestHandler):
        def do_PUT(self):
            if self.rfile.read(512):
                hit.set()
            self.close_connection = True

        def log_message(self, *_):
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), DropUpload) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        broken = boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{server.server_port}",
            aws_access_key_id="fixture",
            aws_secret_access_key="fixture",
            config=Config(retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=2),
            region_name="us-east-1",
        )
        real_put = back.put_new
        calls = []

        def possibly_drop(key, stream, size, content_type):
            if not calls:
                calls.append(key)
                return ObjectStore(back.bucket, broken).put_new(key, stream, size, content_type)
            return real_put(key, stream, size, content_type)

        monkeypatch.setattr(back, "put_new", possibly_drop)
        try:
            result, _ = backup_objects(
                source, back, tmp_path, prefix=PREFIX, local_drill=True, budget=TransferBudget(20, 0)
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert hit.is_set() and result["status"] == "COMPLETE"
    assert result["copied_count"] == 1
    assert source.head("data.mp4").size == len(contents)


def test_s3_permission_and_server_error_are_not_missing(stores, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    source, _, _ = stores
    seed_work("denied", 10)

    for status in (403, 500):

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_error(status)

            def log_message(self, *_):
                return

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            service = Thread(target=server.serve_forever, daemon=True)
            service.start()
            try:
                client = new_s3_client(f"http://127.0.0.1:{server.server_port}", "us-east-1", "test", "test")
                code, report, _ = run_check(ObjectStore(source.bucket, client), tmp_path)
                assert code == 2 and report["summary"]["storage_error_count"] == 1
                assert report["summary"]["missing_count"] == 0
            finally:
                server.shutdown()
                service.join(timeout=2)


def test_direct_s3_download_headers_ranges_and_cdn(stores, tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import httpx

    from app.core import storage

    source, _, _ = stores
    # A disk-backed fixture drives the signed path without an API response body.
    name = "old/作品.mp4"
    size = 8 * 1024 * 1024
    disk = tmp_path / "download.mp4"
    with disk.open("xb") as stream:
        stream.truncate(size)
    with disk.open("rb") as stream:
        source.client.put_object(
            Bucket=source.bucket, Key=name, Body=stream, ContentLength=size, ContentType="video/mp4"
        )
    monkeypatch.setattr(storage, "_CLIENT", source.client)
    original = storage.presigned_get_url(
        source.bucket, name, filename="考生作品.mp4", content_type="video/mp4"
    )
    headers = {"Bucket": source.bucket, "Key": name}
    head_url = source.client.generate_presigned_url("head_object", Params=headers, ExpiresIn=30)
    with httpx.Client(trust_env=False, timeout=15) as http:
        head = http.head(head_url)
        assert head.status_code == 200 and int(head.headers["Content-Length"]) == size
        assert head.headers["Content-Type"] == "video/mp4"
        for start, end in ((0, 1023), (6144, 7167)):
            part = http.get(original, headers={"Range": f"bytes={start}-{end}"})
            assert part.status_code == 206 and len(part.content) == 1024
            assert part.headers["Content-Range"] == f"bytes {start}-{end}/{size}"
        assert http.get(original, headers={"Range": f"bytes={size}-"}).status_code == 416
        with http.stream("GET", original) as result:
            assert result.status_code == 200 and int(result.headers["Content-Length"]) == size
            assert sum(len(chunk) for chunk in result.iter_bytes(256 * 1024)) == size
    monkeypatch.setattr(storage.settings, "CDN_BASE_URL", "https://cdn.example.test")
    rewritten = storage.apply_cdn_host(original)
    before, after = urlsplit(original), urlsplit(rewritten)
    assert after.netloc == "cdn.example.test" and after.scheme == "https"
    assert after.path == before.path and after.query == before.query
    assert "UTF-8" in parse_qs(after.query)["response-content-disposition"][0]


def test_backup_target_connection_failure_never_mutates_source(stores, tmp_path):
    source, back, _ = stores
    content = b"data stays in source"
    put(source, "original", content)
    seed_work("original", len(content), digest_bytes(content))
    with socket.socket() as unopened:
        unopened.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unopened.getsockname()[1]}"
        unreachable = ObjectStore(back.bucket, new_s3_client(endpoint, "us-east-1", "fixture", "fixture"))
        result, _ = backup_objects(
            source, unreachable, tmp_path, prefix=PREFIX, local_drill=True, budget=TransferBudget(15, 0)
        )
    assert result["status"] == "FAILED" and result["verified_count"] == 0
    assert read_object(source, "original", len(content), TransferBudget(10, 0)).sha256 == digest_bytes(
        content
    )
