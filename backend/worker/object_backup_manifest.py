"""Files and metadata used to seal an Issue 008 backup."""

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.object_integrity import ObjectDigest, stream_digest
from app.core.storage import ObjectConflict
from worker.object_inventory import DIGEST
from worker.object_transfer import MAX_OBJECT, file_digest, read_object

BACKUP_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}")
SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SNAPSHOT_FILES = ("database.sql.gz", "metadata.txt", "SHA256SUMS")
MANIFEST_LIMIT = 256 * 1024 * 1024
SMALL_FILE = 64 * 1024


def timestamp():
    return datetime.now(UTC).isoformat()


def new_backup_id():
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:16]


def write_row(stream, row):
    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def small_file(path: Path):
    if not path.is_file() or not 0 < path.stat().st_size <= SMALL_FILE:
        raise ValueError("missing or oversized metadata file")
    return path.read_text(encoding="utf-8")


def snapshot_info(directory: Path):
    if not SNAPSHOT_ID.fullmatch(directory.name):
        raise ValueError("invalid MySQL backup ID")
    metadata = dict(line.split("=", 1) for line in small_file(directory / "metadata.txt").splitlines())
    if metadata.get("schema") != "i2v-mysql-backup-v1" or not metadata.get("database"):
        raise ValueError("invalid MySQL backup metadata")
    datetime.strptime(metadata.get("started_utc", ""), "%Y%m%dT%H%M%SZ")
    checksums = {}
    for line in small_file(directory / "SHA256SUMS").splitlines():
        parts = line.split()
        if len(parts) != 2 or not DIGEST.fullmatch(parts[0]) or parts[1] in checksums:
            raise ValueError("invalid MySQL checksum list")
        checksums[parts[1]] = parts[0]
    if set(checksums) != set(SNAPSHOT_FILES[:2]):
        raise ValueError("invalid MySQL archive filenames")
    archive = directory / "database.sql.gz"
    if not 0 < archive.stat().st_size <= MAX_OBJECT:
        raise ValueError("invalid MySQL archive size")
    digests = {name: file_digest(directory / name) for name in SNAPSHOT_FILES}
    if any(digests[name].sha256 != expected for name, expected in checksums.items()):
        raise ValueError("MySQL backup checksum mismatch")
    return metadata, digests


def remote_small(store, key, budget, *, maximum=SMALL_FILE):
    budget.check()
    with closing(store.chunks(key)) as chunks:
        # Transfer metadata without buffering any media objects.
        from io import BytesIO

        output = BytesIO()
        stream_digest(chunks, maximum, output=output, check=budget.check)
        return output.getvalue()


def manifest_rows(path):
    with path.open(encoding="utf-8") as stream:
        while line := stream.readline(SMALL_FILE + 1):
            if len(line) > SMALL_FILE:
                raise ValueError("overlong manifest line")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid manifest entry")
            yield row


def verify_seal(store, prefix, backup_id, directory, budget):
    if not BACKUP_ID.fullmatch(backup_id):
        raise ValueError("invalid backup ID")
    anchor = f"{prefix}/runs/{backup_id}"
    declared = remote_small(store, f"{anchor}/seal.sha256", budget).decode("ascii").strip()
    seal_bytes = remote_small(store, f"{anchor}/seal.json", budget)
    from hashlib import sha256

    if not DIGEST.fullmatch(declared) or sha256(seal_bytes).hexdigest() != declared:
        raise ValueError("backup seal SHA-256 mismatch")
    seal = json.loads(seal_bytes)
    if (
        seal["schema_version"] != 1
        or seal["backup_id"] != backup_id
        or seal["status"] != "COMPLETE"
        or seal["backup_bucket"] != store.bucket
        or seal["backup_prefix"] != prefix
        or seal["failed_count"] != 0
        or seal["verified_count"] != seal["object_count"]
    ):
        raise ValueError("backup is incomplete or manifest targets differ")
    path = directory / "objects.jsonl"
    with path.open("xb") as output:
        digest = read_object(
            store,
            f"{anchor}/objects.jsonl",
            seal["object_manifest_size"],
            budget,
            output=output,
            maximum=MANIFEST_LIMIT,
        )
    if digest != ObjectDigest(seal["object_manifest_size"], seal["object_manifest_hash"]):
        raise ObjectConflict("object_manifest_sha256_mismatch")
    return seal, path, anchor
