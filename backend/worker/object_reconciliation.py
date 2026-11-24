"""Read-only, resumable video-object reconciliation."""

import argparse
import csv
import json
import os
import signal
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.object_integrity import ObjectSizeMismatch
from app.core.storage import ObjectStore, StorageUnavailable
from app.services.execution_lease import ACTIVE_WORK
from worker.object_inventory import GENERATED, object_claims, successful_page, work_links
from worker.object_transfer import TransferBudget, read_object

FIELDS = (
    "work_id",
    "task_no",
    "object_key",
    "expected_size",
    "actual_size",
    "expected_sha256",
    "actual_sha256",
    "classification",
    "detected_at",
    "finished_at",
    "error_reason",
)
ANOMALIES = {
    "MISSING_OBJECT",
    "SIZE_MISMATCH",
    "CHECKSUM_MISMATCH",
    "ORPHAN_OBJECT",
    "REFERENCE_CONFLICT",
}
COUNTERS = {
    "HEALTHY": "healthy_count",
    "MISSING_OBJECT": "missing_count",
    "SIZE_MISMATCH": "size_mismatch_count",
    "CHECKSUM_MISMATCH": "checksum_mismatch_count",
    "ORPHAN_OBJECT": "orphan_count",
    "ACTIVE_CHECKPOINT": "active_checkpoint_count",
    "LEGACY_UNVERIFIED": "legacy_unverified_count",
    "STORAGE_UNAVAILABLE": "storage_error_count",
    "DATABASE_UNAVAILABLE": "database_error_count",
    "REFERENCE_CONFLICT": "reference_conflict_count",
    "METADATA_MATCH": "metadata_match_count",
    "GRACE_PERIOD": "grace_period_count",
    "UNCLASSIFIED_OBJECT": "unclassified_object_count",
}


def utc() -> str:
    return datetime.now(UTC).isoformat()


def finding(status, *, ref=None, key=None, size=None, digest=None, reason=None, work_id=None):
    return {
        "work_id": ref.work_id if ref else work_id,
        "task_no": ref.task_no if ref else None,
        "object_key": ref.key if ref else key,
        "expected_size": ref.size if ref else None,
        "actual_size": size,
        "expected_sha256": ref.digest if ref else None,
        "actual_sha256": digest,
        "classification": status,
        "detected_at": utc(),
        "finished_at": ref.finished_at.replace(tzinfo=UTC).isoformat() if ref and ref.finished_at else None,
        "error_reason": reason,
    }


def check_reference(ref, *, store, budget, full, links):
    if not ref.key:
        return finding("MISSING_OBJECT", ref=ref, reason="missing_video_key")
    if ref.checkpoint_conflict or len(links.get(ref.key, ())) > 1:
        return finding("REFERENCE_CONFLICT", ref=ref, reason="checkpoint_or_multiple_work_conflict")
    actual_size = None
    try:
        budget.check()
        metadata = store.head(ref.key)
        if metadata is None:
            return finding("MISSING_OBJECT", ref=ref)
        actual_size = metadata.size
        if actual_size <= 0 or ref.size is not None and ref.size != actual_size:
            return finding("SIZE_MISMATCH", ref=ref, size=actual_size)
        digest = read_object(store, ref.key, actual_size, budget).sha256 if full else None
        if not ref.digest:
            status = "LEGACY_UNVERIFIED"
        elif not full:
            status = "METADATA_MATCH"
        else:
            status = "HEALTHY" if ref.digest == digest else "CHECKSUM_MISMATCH"
        reason = "computed_without_trusted_baseline" if full and not ref.digest else None
        return finding(status, ref=ref, size=actual_size, digest=digest, reason=reason)
    except ObjectSizeMismatch:
        return finding("SIZE_MISMATCH", ref=ref, size=actual_size, reason="stream_length_or_limit")
    except StorageUnavailable as error:
        return finding("STORAGE_UNAVAILABLE", ref=ref, reason=str(error))


def scan_object_page(objects, grace_days, sessions):
    keys = {item.key for item in objects}
    claims, owners = object_claims(keys, sessions)
    links = work_links(keys, sessions)
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)
    for item in objects:
        known = claims.get(item.key, ())
        match = GENERATED.fullmatch(item.key)
        inferred = int(match[1]) if match and len(match[1]) <= 19 else None
        if any(active for _, active, _, _ in known) or owners.get(inferred) in ACTIVE_WORK:
            owner = next((work_id for work_id, active, _, _ in known if active), inferred)
            yield finding("ACTIVE_CHECKPOINT", key=item.key, size=item.size, work_id=owner)
        elif item.key in links:
            continue
        elif item.key.startswith("videos/mock/") or not match:
            yield finding("UNCLASSIFIED_OBJECT", key=item.key, size=item.size, reason="mock_or_unknown_key")
        elif item.modified >= cutoff:
            yield finding("GRACE_PERIOD", key=item.key, size=item.size)
        else:
            yield finding(
                "ORPHAN_OBJECT", key=item.key, size=item.size, work_id=inferred, reason="candidate_only"
            )


class Report:
    def __init__(self, stack, output, run_id):
        output.mkdir(parents=True, exist_ok=True)
        self.json_path = output / f"{run_id}.json"
        self.csv_path = output / f"{run_id}.csv"
        self.cursor_path = output / f"{run_id}.cursor.json"
        self.json = stack.enter_context(self.json_path.open("x", encoding="utf-8"))
        csv_file = stack.enter_context(self.csv_path.open("x", encoding="utf-8", newline=""))
        self.csv_file = csv_file
        self.csv = csv.DictWriter(csv_file, fieldnames=FIELDS)
        self.csv.writeheader()
        self.json.write('{"findings":[\n')
        self.first = True
        self.counts = Counter()

    def add(self, row):
        self.counts[row["classification"]] += 1
        if not self.first:
            self.json.write(",\n")
        self.first = False
        self.json.write(json.dumps(row, ensure_ascii=False))
        self.csv.writerow(
            {
                key: (
                    "'" + value
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                )
                for key, value in row.items()
            }
        )

    def save_cursor(self, summary):
        self.json.flush()
        self.csv_file.flush()
        checkpoint = {key: summary[key] for key in ("bucket", "mode", "grace_days", "cursor")}
        staged = self.cursor_path.with_suffix(".tmp")
        staged.write_text(json.dumps(checkpoint), encoding="utf-8")
        staged.replace(self.cursor_path)

    def finish(self, summary):
        summary.update({counter: self.counts[status] for status, counter in COUNTERS.items()})
        self.json.write('],"summary":' + json.dumps(summary, ensure_ascii=False) + "}\n")
        return summary


def reconcile(
    store,
    output: Path,
    *,
    mode="quick",
    batch_size=100,
    limit=None,
    concurrency=1,
    rate=256 * 1024,
    seconds=3600,
    grace_days=7,
    resume=None,
    sessions=SyncSessionLocal,
):
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    cursor = {"work_id": 0, "work_done": False, "object_token": None, "object_done": False}
    if resume:
        if resume["mode"] != mode or resume["bucket"] != store.bucket or resume["grace_days"] != grace_days:
            raise ValueError("resume report has a different bucket, mode or grace period")
        cursor.update(resume["cursor"])
        if (
            type(cursor["work_id"]) is not int
            or cursor["work_id"] < 0
            or type(cursor["work_done"]) is not bool
            or type(cursor["object_done"]) is not bool
            or cursor["object_token"] is not None
            and not isinstance(cursor["object_token"], str)
        ):
            raise ValueError("invalid resume cursor")
    summary = {
        "run_id": run_id,
        "started_at": utc(),
        "bucket": store.bucket,
        "mode": mode,
        "grace_days": grace_days,
        "database_records_scanned": 0,
        "objects_scanned": 0,
        "cursor": cursor,
        "interrupted": False,
    }
    budget = TransferBudget(seconds, rate)
    with ExitStack() as stack:
        report = Report(stack, output, run_id)
        pool = ThreadPoolExecutor(max_workers=concurrency)
        stack.callback(pool.shutdown, cancel_futures=True)
        report.save_cursor(summary)
        try:
            while not cursor["work_done"] and (limit is None or summary["database_records_scanned"] < limit):
                budget.check()
                size = min(batch_size, limit - summary["database_records_scanned"]) if limit else batch_size
                refs = successful_page(cursor["work_id"], size, sessions)
                if not refs:
                    cursor["work_done"] = True
                    break
                links = work_links({ref.key for ref in refs if ref.key}, sessions)
                check = partial(check_reference, store=store, budget=budget, full=mode == "full", links=links)
                for ref, row in zip(refs, pool.map(check, refs), strict=True):
                    report.add(row)
                    summary["database_records_scanned"] += 1
                    if row["classification"] == "STORAGE_UNAVAILABLE":
                        raise StorageUnavailable(row["error_reason"])
                    cursor["work_id"] = ref.work_id
                cursor["work_done"] = len(refs) < size
                report.save_cursor(summary)

            while not cursor["object_done"] and (limit is None or summary["objects_scanned"] < limit):
                budget.check()
                size = min(batch_size, limit - summary["objects_scanned"]) if limit else batch_size
                page, next_token = store.page(size, cursor["object_token"])
                for row in scan_object_page(page, grace_days, sessions):
                    report.add(row)
                summary["objects_scanned"] += len(page)
                cursor["object_token"] = next_token
                cursor["object_done"] = next_token is None
                report.save_cursor(summary)
        except SQLAlchemyError:
            report.add(finding("DATABASE_UNAVAILABLE", reason="database_query_failed"))
        except StorageUnavailable as error:
            if not report.counts["STORAGE_UNAVAILABLE"]:
                report.add(finding("STORAGE_UNAVAILABLE", reason=str(error)))
        except KeyboardInterrupt:
            summary["interrupted"] = True
        summary["finished_at"] = utc()
        report.save_cursor(summary)
        report.finish(summary)
        if summary["interrupted"] or summary["storage_error_count"] or summary["database_error_count"]:
            code = 2
        else:
            code = int(any(report.counts[item] for item in ANOMALIES))
    return code, report.json_path, summary


class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage()
        self.exit(3, f"configuration error: {message}\n")


def bounded(low, high):
    def parse(value):
        result = int(value)
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"must be within {low}–{high}")
        return result

    return parse


def interruption(_sig, _frame):
    raise KeyboardInterrupt


def main():
    os.umask(0o077)
    parser = CLIParser(description=__doc__)
    parser.add_argument("--mode", choices=("quick", "full"), default="quick")
    parser.add_argument("--output", type=Path, default=Path("reports/object-reconciliation"))
    parser.add_argument("--limit", type=bounded(1, 10_000_000))
    parser.add_argument("--batch-size", type=bounded(1, 500), default=100)
    parser.add_argument("--max-concurrency", type=bounded(1, 4), default=1)
    parser.add_argument("--rate-kib", type=bounded(1, 1024), default=256)
    parser.add_argument("--timeout", type=bounded(1, 86400), default=3600)
    parser.add_argument("--grace-days", type=bounded(7, 3650), default=7)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Always read-only")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interruption)
    try:
        resume = None
        if args.resume:
            if args.resume.stat().st_size > 65536:
                raise ValueError("resume cursor exceeds size limit")
            resume = json.loads(args.resume.read_text(encoding="utf-8"))
        code, path, summary = reconcile(
            ObjectStore(get_settings().MINIO_BUCKET_VIDEOS),
            args.output,
            mode=args.mode,
            batch_size=args.batch_size,
            limit=args.limit,
            concurrency=args.max_concurrency,
            rate=args.rate_kib * 1024,
            seconds=args.timeout,
            grace_days=args.grace_days,
            resume=resume,
        )
    except (OSError, ValueError, TypeError, KeyError):
        parser.error("invalid resume report, output directory, or settings")
    print(json.dumps({"report": str(path), **summary}, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
