"""Verified object backups with guarded, isolated restore commands."""

import json
import os
import re
import shutil
from ipaddress import ip_address
from itertools import zip_longest
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.object_integrity import ObjectDigest, ObjectSizeMismatch
from app.core.storage import ObjectConflict, ObjectStore, StorageUnavailable, new_s3_client
from worker.media_process import MediaError
from worker.media_validation import validate_output
from worker.object_backup_manifest import (
    MANIFEST_LIMIT,
    SCHEMA_VERSION,
    archive_ready,
    SNAPSHOT_ID,
    file_digest,
    manifest_rows,
    new_backup_id,
    snapshot_info,
    timestamp,
    verify_seal,
    write_row,
)
from worker.object_inventory import DIGEST, active_count, successful_refs
from worker.object_reconciliation import CLIParser, bounded
from worker.object_transfer import MAX_OBJECT, TransferBudget, read_object, store_verified

PREFIX = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")


def reserve_disk(directory, size):
    if not 0 < size <= MAX_OBJECT:
        raise ObjectSizeMismatch("object is empty or exceeds the transfer limit")
    if shutil.disk_usage(directory).free < size + 64 * 1024 * 1024:
        raise StorageUnavailable("scratch_disk_budget")


def data_key(prefix, digest):
    return f"{prefix}/data/sha256/{digest}"


def safe_error(error):
    if isinstance(error, (StorageUnavailable, ObjectConflict, ObjectSizeMismatch)):
        return str(error)
    if isinstance(error, MediaError):
        return error.reason
    return type(error).__name__


def copy_reference(ref, source, backup, prefix, scratch, budget):
    if not ref.key:
        raise ObjectConflict("missing_video_key")
    if ref.checkpoint_conflict:
        raise ObjectConflict("contradictory_work_checkpoint")
    budget.check()
    meta = source.head(ref.key)
    if meta is None:
        raise ObjectConflict("source_object_missing")
    if ref.size is not None and meta.size != ref.size:
        raise ObjectConflict("source_size_differs_from_database")
    reserve_disk(scratch, meta.size)
    with TemporaryDirectory(dir=scratch) as temporary:
        path = Path(temporary) / "video"
        with path.open("xb") as stream:
            actual = read_object(source, ref.key, meta.size, budget, output=stream)
        if ref.digest and ref.digest != actual.sha256:
            raise ObjectConflict("source_sha256_differs_from_database")
        key = data_key(prefix, actual.sha256)
        created = store_verified(backup, key, path, actual, budget, "video/mp4")
    return meta, actual, created


def _two_attempts(transfer, budget):
    try:
        return transfer()
    except StorageUnavailable:
        budget.check()
        return transfer()


def database_snapshot(directory, confirmed, sessions):
    if not directory:
        if confirmed:
            raise ValueError("confirmation requires a database snapshot")
        return None
    if not confirmed:
        raise ValueError("confirm use of an isolated database restored from this exact snapshot")
    metadata, digests = snapshot_info(directory)
    with sessions() as session:
        name = session.scalar(select(func.database()))
    if not name or name.lower() == metadata["database"].lower():
        raise ValueError("database inventory must come from the isolated restored snapshot")
    if active_count(sessions):
        raise ValueError("backup snapshot contains queued or running Works")
    return metadata, digests


def backup_objects(
    source,
    backup,
    output: Path,
    *,
    prefix="i2v-object-backups",
    batch_size=100,
    budget=None,
    snapshot=None,
    confirmed=False,
    local_drill=False,
    failure_domain=None,
    sessions=SyncSessionLocal,
):
    backup_id = new_backup_id()
    output.mkdir(parents=True, exist_ok=True)
    directory = output / backup_id
    directory.mkdir(mode=0o700)
    manifest = directory / "objects.jsonl"
    seal_path = directory / "seal.json"
    budget = budget or TransferBudget(6 * 3600, 192 * 1024)
    if not PREFIX.fullmatch(prefix):
        raise ValueError("invalid backup key prefix")
    if (
        backup
        and source.bucket == backup.bucket
        and source.client.meta.endpoint_url == backup.client.meta.endpoint_url
    ):
        raise ValueError("backup and source must use different locations")
    info = database_snapshot(snapshot, confirmed, sessions) if backup is not None else None
    anchor = f"{prefix}/runs/{backup_id}"
    summary = {
        "schema_version": SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at": timestamp(),
        "object_snapshot_time": timestamp(),
        "source_bucket": source.bucket,
        "backup_bucket": backup.bucket if backup else None,
        "backup_prefix": prefix,
        "destination_kind": "UNCONFIGURED" if backup is None else "LOCAL_DRILL" if local_drill else "OFFSITE",
        "destination_failure_domain": failure_domain if not local_drill else None,
        "status": "RUNNING" if backup else "OFFSITE_NOT_CONFIGURED",
        "consistency_status": "INCONSISTENT" if backup else "NOT_APPLICABLE",
        "database_backup_id": snapshot.name if snapshot else None,
        "database_snapshot_time": None,
        "database_backup_hash": None,
        "database_archive": None,
        "object_count": 0,
        "verified_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "copied_count": 0,
        "verified_bytes": 0,
        "last_successful_object": None,
    }
    if info:
        meta, digests = info
        summary["database_snapshot_time"] = meta["started_utc"]
        summary["database_backup_hash"] = digests["database.sql.gz"].sha256
    seal_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if backup is None:
        return summary, directory

    start = monotonic()
    stopped = False
    try:
        with manifest.open("x", encoding="utf-8", newline="\n") as stream:
            for ref in successful_refs(batch_size, sessions):
                budget.check()
                summary["object_count"] += 1
                row = {
                    "schema_version": SCHEMA_VERSION,
                    "work_id": ref.work_id,
                    "task_no": ref.task_no,
                    "object_key": ref.key,
                    "kind": ref.kind,
                    "duration": ref.duration,
                }
                try:
                    meta, actual, created = _two_attempts(
                        lambda: copy_reference(ref, source, backup, prefix, directory, budget), budget
                    )
                except (ObjectConflict, ObjectSizeMismatch, StorageUnavailable) as error:
                    summary["failed_count"] += 1
                    row.update(status="FAILED", error_reason=safe_error(error))
                    write_row(stream, row)
                    if (
                        isinstance(error, ObjectConflict)
                        and safe_error(error) == "destination_size_or_sha256_mismatch"
                    ):
                        stopped = True
                        break
                    continue
                row.update(
                    status="VERIFIED",
                    size=actual.size,
                    sha256=actual.sha256,
                    modified_at=meta.modified.isoformat(),
                    checksum_provenance="execution" if ref.digest else "computed_during_backup",
                )
                summary["verified_count"] += 1
                summary["copied_count" if created else "skipped_count"] += 1
                summary["verified_bytes"] += actual.size
                summary["last_successful_object"] = ref.key
                write_row(stream, row)
        summary["inventory_complete"] = not stopped
        if not stopped and not summary["failed_count"] and info:
            archive = {}
            for name, expected in digests.items():
                if not 0 < expected.size <= MAX_OBJECT:
                    raise ObjectSizeMismatch("database archive exceeds transfer limit")
                key = f"{anchor}/database/{name}"
                store_verified(backup, key, snapshot / name, expected, budget)
                archive[name] = {"size": expected.size, "sha256": expected.sha256}
            summary["database_archive"] = archive
        manifest_digest = file_digest(manifest)
        summary.update(object_manifest_hash=manifest_digest.sha256, object_manifest_size=manifest_digest.size)
        if manifest_digest.size > MANIFEST_LIMIT:
            raise ObjectSizeMismatch("manifest exceeds supported size")
        if stopped or summary["failed_count"]:
            summary["status"] = "PARTIAL" if summary["verified_count"] else "FAILED"
        else:
            store_verified(
                backup, f"{anchor}/objects.jsonl", manifest, manifest_digest, budget, "application/jsonl"
            )
            if info:
                if not archive_ready(summary):
                    raise ValueError("verified database archive is incomplete")
                summary.update(status="COMPLETE", consistency_status="CONSISTENT")
            else:
                summary.update(status="OBJECTS_COMPLETE", consistency_status="OBJECTS_ONLY")
            summary["finished_at"] = timestamp()
            summary["elapsed_seconds"] = round(monotonic() - start, 3)
            pending = directory / "pending-seal.json"
            pending.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            checksum = directory / "seal.sha256"
            checksum.write_text(file_digest(pending).sha256 + "\n", encoding="ascii")
            store_verified(
                backup, f"{anchor}/seal.json", pending, file_digest(pending), budget, "application/json"
            )
            store_verified(
                backup, f"{anchor}/seal.sha256", checksum, file_digest(checksum), budget, "text/plain"
            )
    except (
        OSError,
        ValueError,
        StorageUnavailable,
        SQLAlchemyError,
        ObjectSizeMismatch,
        ObjectConflict,
        KeyboardInterrupt,
    ) as error:
        summary["status"] = "PARTIAL" if summary["verified_count"] else "FAILED"
        summary["consistency_status"] = "INCONSISTENT"
        summary["run_error"] = safe_error(error)
    summary.setdefault("finished_at", timestamp())
    summary.setdefault("elapsed_seconds", round(monotonic() - start, 3))
    seal_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary, directory


def _restore_rows(manifest, seal, batch_size, sessions, samples=0):
    count = 0
    selected = []
    candidates = 0
    random = Random(seal["backup_id"])

    for row, ref in zip_longest(manifest_rows(manifest), successful_refs(batch_size, sessions)):
        if (
            not row
            or not ref
            or row.get("status") != "VERIFIED"
            or not DIGEST.fullmatch(row.get("sha256") or "")
        ):
            raise ValueError("manifest and restored database references differ")
        if (
            row.get("work_id") != ref.work_id
            or row.get("task_no") != ref.task_no
            or not ref.key
            or row.get("object_key") != ref.key
            or ref.checkpoint_conflict
            or (ref.size is not None and row.get("size") != ref.size)
            or (ref.digest and row["sha256"] != ref.digest)
            or row.get("duration") != ref.duration
            or type(row.get("size")) is not int
            or not 0 < row["size"] <= MAX_OBJECT
        ):
            raise ValueError("manifest and restored database references differ")
        count += 1
        if samples and ref.kind != "mock":
            candidates += 1
            if len(selected) < samples:
                selected.append(ref.work_id)
            elif (position := random.randrange(candidates)) < samples:
                selected[position] = ref.work_id
    if count != seal["object_count"]:
        raise ValueError("manifest object count mismatch")
    return set(selected)


def _restore_one(row, backup, target, prefix, scratch, budget, *, sample=False):
    expected = ObjectDigest(row["size"], row["sha256"])
    reserve_disk(scratch, expected.size)
    with TemporaryDirectory(dir=scratch) as temporary:
        path = Path(temporary) / "restored.mp4"
        with path.open("xb") as output:
            actual = read_object(
                backup, data_key(prefix, expected.sha256), expected.size, budget, output=output
            )
        if actual != expected:
            raise ObjectConflict("backup_object_sha256_mismatch")
        created = store_verified(target, row["object_key"], path, expected, budget, "video/mp4")
        if sample:
            # Validate bytes re-read from the target, not just the source download.
            with path.open("wb") as output:
                if read_object(target, row["object_key"], expected.size, budget, output=output) != expected:
                    raise ObjectConflict("restored_object_sha256_mismatch")
            if row.get("duration"):
                validate_output(path, row["duration"], None, budget.deadline)
            else:
                raise ValueError("sample has no verifiable target duration")
    return created


def restore_objects(
    backup,
    target,
    prefix,
    backup_id,
    output: Path,
    *,
    budget=None,
    batch_size=100,
    samples=1,
    allow_unlinked_drill=False,
    sessions=SyncSessionLocal,
):
    settings = get_settings()
    if settings.is_production:
        raise ValueError("restore requires an isolated application/database configuration")
    budget = budget or TransferBudget(6 * 3600, 192 * 1024)
    if not PREFIX.fullmatch(prefix):
        raise ValueError("invalid backup key prefix")
    if backup.bucket == target.bucket:
        raise ValueError("restore target bucket must differ from backup bucket")
    target_name = settings.MINIO_BUCKET_VIDEOS
    if target.bucket == target_name:
        raise ValueError("restore target must differ from the configured application bucket")
    output.mkdir(parents=True, exist_ok=True)
    ird = output / f"restore-{new_backup_id()}"
    ird.mkdir(mode=0o700)
    report_path = ird / "report.json"
    summary = {
        "backup_id": backup_id,
        "target_bucket": target.bucket,
        "restore_mode": "LOCAL_DRILL" if allow_unlinked_drill else "ISOLATED_RESTORE",
        "started_at": timestamp(),
        "status": "FAILED",
        "restored_count": 0,
        "already_present_count": 0,
        "media_verified_count": 0,
    }
    try:
        with TemporaryDirectory(dir=ird) as temporary:
            seal, manifest, anchor = verify_seal(
                backup,
                prefix,
                backup_id,
                Path(temporary),
                budget,
                local_drill=allow_unlinked_drill,
                allow_objects_only=allow_unlinked_drill,
            )
            if seal["source_bucket"] == target.bucket:
                raise ValueError("cannot restore into the original bucket")
            if seal["consistency_status"] == "CONSISTENT":
                verify_database_archive(backup, seal, anchor, budget)
            else:
                summary["restore_mode"] = "LOCAL_DRILL_OBJECTS_ONLY"
            if active_count(sessions):
                raise ValueError("target database contains active Works")
            selected = _restore_rows(manifest, seal, batch_size, sessions, samples=samples)
            with (ird / "objects.jsonl").open("x", encoding="utf-8", newline="\n") as report:
                for row in manifest_rows(manifest):
                    sample = row["work_id"] in selected
                    created = _two_attempts(
                        lambda: _restore_one(row, backup, target, prefix, ird, budget, sample=sample), budget
                    )
                    summary["restored_count" if created else "already_present_count"] += 1
                    summary["media_verified_count"] += int(sample)
                    write_row(
                        report,
                        {
                            **row,
                            "restore_status": "RESTORED" if created else "ALREADY_PRESENT",
                            "media_verified": sample,
                        },
                    )
            _restore_rows(manifest, seal, batch_size, sessions)
            summary["object_count"] = seal["object_count"]
            summary["status"] = "COMPLETE"
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        StorageUnavailable,
        SQLAlchemyError,
        ObjectConflict,
        MediaError,
        KeyboardInterrupt,
    ) as error:
        summary["error"] = safe_error(error)
    summary["finished_at"] = timestamp()
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary, report_path


def verify_database_archive(backup, seal, anchor, budget, *, destination=None):
    for name, entry in seal["database_archive"].items():
        expected = ObjectDigest(entry["size"], entry["sha256"])
        key = f"{anchor}/database/{name}"
        if destination is None:
            actual = read_object(backup, key, expected.size, budget)
        else:
            reserve_disk(destination, expected.size)
            with (destination / name).open("xb") as output:
                actual = read_object(backup, key, expected.size, budget, output=output)
        if actual != expected:
            raise ObjectConflict("database_archive_sha256_mismatch")


def fetch_database(backup, prefix, backup_id, destination, budget, *, local_drill=False):
    if not PREFIX.fullmatch(prefix):
        raise ValueError("invalid backup key prefix")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    with TemporaryDirectory(dir=destination) as temporary:
        seal, _, anchor = verify_seal(
            backup, prefix, backup_id, Path(temporary), budget, local_drill=local_drill
        )
        directory = destination / seal["database_backup_id"]
        directory.mkdir(mode=0o700)
        verify_database_archive(backup, seal, anchor, budget, destination=directory)
        snapshot_info(directory)
        return directory


def configured_store(role, *, allow_local):
    prefix = f"I2V_{role}_S3_"
    names = ("ENDPOINT", "BUCKET", "ACCESS_KEY", "SECRET_KEY")
    endpoint, bucket, access, secret = (os.getenv(prefix + name, "").strip() for name in names)
    if not any((endpoint, bucket, access, secret)):
        return None
    if not all((endpoint, bucket, access, secret)):
        raise ValueError("incomplete S3 configuration")
    parsed = urlsplit(endpoint)
    _ = parsed.port
    if (
        parsed.scheme not in ({"http", "https"} if allow_local else {"https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in endpoint
        or "#" in endpoint
        or any(char.isspace() for char in endpoint)
        or "\\" in endpoint
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("invalid S3 endpoint or missing TLS")
    if not allow_local:
        hostname = parsed.hostname.rstrip(".")
        try:
            address = ip_address(hostname)
        except ValueError:
            address = None
        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
            or address is not None
            and (
                address.is_loopback
                or getattr(address, "ipv4_mapped", None) is not None
                and address.ipv4_mapped.is_loopback
            )
        ):
            raise ValueError("loopback cannot be labeled offsite")
    return ObjectStore(
        bucket, new_s3_client(endpoint, os.getenv(prefix + "REGION", "us-east-1"), access, secret)
    )


def main():
    os.umask(0o077)
    parser = CLIParser(description=__doc__)
    parser.add_argument("--prefix", default=os.getenv("I2V_BACKUP_S3_PREFIX", "i2v-object-backups"))
    parser.add_argument("--rate-kib", type=bounded(1, 1024), default=192)
    parser.add_argument("--timeout", type=bounded(1, 86400), default=21600)
    parser.add_argument("--local-drill", action="store_true")
    commands = parser.add_subparsers(dest="operation", required=True, parser_class=CLIParser)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--database-snapshot", type=Path)
    backup_parser.add_argument("--confirm-database-restored", action="store_true")
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("backup_id")
    restore_parser.add_argument("--output", type=Path, required=True)
    restore_parser.add_argument("--confirm-isolated-target", action="store_true", required=True)
    restore_parser.add_argument("--media-samples", type=bounded(1, 100), default=1)
    fetch_parser = commands.add_parser("fetch-database")
    fetch_parser.add_argument("backup_id")
    fetch_parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    budget = TransferBudget(args.timeout, args.rate_kib * 1024)
    try:
        settings = get_settings()
        backup = configured_store("BACKUP", allow_local=args.local_drill)
        if args.operation == "backup":
            domain = os.getenv("I2V_BACKUP_FAILURE_DOMAIN", "")
            if backup and not args.local_drill:
                if not SNAPSHOT_ID.fullmatch(domain):
                    raise ValueError("set the independent failure domain identifier")
                source_host = urlsplit(settings.minio_endpoint_url).hostname
                if urlsplit(backup.client.meta.endpoint_url).hostname == source_host:
                    raise ValueError("same source host is not an offsite destination")
            if (
                backup
                and backup.client.meta.endpoint_url.rstrip("/") == settings.minio_endpoint_url.rstrip("/")
                and (backup.bucket == settings.MINIO_BUCKET_VIDEOS)
            ):
                raise ValueError("backup and source are identical")
            summary, report = backup_objects(
                ObjectStore(settings.MINIO_BUCKET_VIDEOS),
                backup,
                args.output,
                prefix=args.prefix,
                budget=budget,
                snapshot=args.database_snapshot,
                confirmed=args.confirm_database_restored,
                local_drill=args.local_drill,
                failure_domain=domain,
            )
        elif backup is None:
            print("OFFSITE_NOT_CONFIGURED")
            return 3
        elif args.operation == "fetch-database":
            archive = fetch_database(
                backup, args.prefix, args.backup_id, args.destination, budget, local_drill=args.local_drill
            )
            if args.local_drill:
                print(json.dumps({"restore_mode": "LOCAL_DRILL", "archive": str(archive)}))
            else:
                print(archive)
            return 0
        else:
            if settings.is_production:
                raise ValueError("restore requires an isolated application/database configuration")
            target = configured_store("RESTORE", allow_local=args.local_drill)
            if target is None:
                raise ValueError("configure a dedicated restore S3 target")
            summary, report = restore_objects(
                backup,
                target,
                args.prefix,
                args.backup_id,
                args.output,
                budget=budget,
                samples=args.media_samples,
                allow_unlinked_drill=args.local_drill,
            )
    except (ValueError, KeyError, TypeError):
        parser.error("invalid snapshot, manifest, or endpoint configuration")
    except (StorageUnavailable, SQLAlchemyError, ObjectConflict, OSError, KeyboardInterrupt) as error:
        print(json.dumps({"status": "FAILED", "error": safe_error(error)}))
        return 2
    print(json.dumps({**summary, "report": str(report)}, ensure_ascii=False))
    return {"COMPLETE": 0, "OFFSITE_NOT_CONFIGURED": 3, "OBJECTS_COMPLETE": 4}.get(summary["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
