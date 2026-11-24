"""Shared bounded transfers, read-back verification and incremental object writes."""

from contextlib import closing
from io import BufferedReader
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

from app.core.object_integrity import ObjectDigest, ObjectSizeMismatch, stream_digest
from app.core.storage import ObjectConflict, ObjectStore, StorageUnavailable

CHUNK = 256 * 1024
MAX_OBJECT = 1024 * 1024 * 1024


class TransferBudget:
    def __init__(self, seconds: int, rate: int):
        self.deadline = monotonic() + seconds
        self.rate = rate
        self.next_chunk = monotonic()
        self.lock = Lock()

    def check(self, size=0):
        now = monotonic()
        if now >= self.deadline:
            raise StorageUnavailable("transfer_deadline")
        if size and self.rate:
            with self.lock:
                self.next_chunk = max(now, self.next_chunk) + size / self.rate
                until = self.next_chunk
            if until >= self.deadline:
                raise StorageUnavailable("transfer_deadline")
            sleep(max(0, until - monotonic()))


class BudgetFile(BufferedReader):
    def __init__(self, path: Path, budget: TransferBudget):
        super().__init__(path.open("rb", buffering=0))
        self.budget = budget

    def read(self, size=-1):
        self.budget.check()
        chunk = super().read(size)
        self.budget.check(len(chunk))
        return chunk


def file_digest(path: Path) -> ObjectDigest:
    with path.open("rb") as stream:
        return stream_digest(iter(lambda: stream.read(CHUNK), b""), path.stat().st_size)


def read_object(
    store: ObjectStore, key: str, size: int, budget: TransferBudget, *, output=None, maximum=MAX_OBJECT
) -> ObjectDigest:
    budget.check()
    if not 0 <= size <= maximum:
        raise ObjectSizeMismatch("object exceeds configured transfer budget")
    with closing(store.chunks(key)) as chunks:
        result = stream_digest(chunks, size, output=output, check=budget.check)
    if result.size != size:
        raise ObjectSizeMismatch("object length changed during read")
    return result


def store_verified(
    store: ObjectStore,
    key: str,
    path: Path,
    expected: ObjectDigest,
    budget: TransferBudget,
    content_type="application/octet-stream",
) -> bool:
    """Upload if absent, then independently verify. Never overwrite on retries."""
    budget.check()
    existing = store.head(key)
    created = False
    if existing is None:
        with BudgetFile(path, budget) as stream:
            created = store.put_new(key, stream, expected.size, content_type)
        existing = store.head(key)
    try:
        matches = (
            existing is not None
            and existing.size == expected.size
            and read_object(store, key, expected.size, budget, maximum=expected.size) == expected
        )
    except ObjectSizeMismatch:
        matches = False
    if not matches:
        raise ObjectConflict("destination_size_or_sha256_mismatch")
    return created
