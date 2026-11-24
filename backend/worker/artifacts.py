"""Store and recover lease-fenced, media-validated artifacts."""

import logging
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from io import BufferedReader
from pathlib import Path
from time import monotonic

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.storage import iter_object_chunks, object_size, upload_file
from app.services.execution_lease import ExecutionLease, active_work, check_lease
from worker.media_process import MediaError, check_deadline
from worker.media_validation import MAX_VIDEO_BYTES, validate_output
from worker.media_workspace import ensure_space

logger = logging.getLogger(__name__)
CHUNK_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class Artifact:
    key: str
    size: int
    digest: str


def _checkpoint(lease: ExecutionLease, artifact: Artifact | None = None) -> Artifact | None:
    with SyncSessionLocal.begin() as session:
        work = active_work(session, lease.work_id)
        row = check_lease(session, work, lease)
        if artifact is not None:
            row.artifact_key, row.artifact_size, row.artifact_sha256 = (
                artifact.key,
                artifact.size,
                artifact.digest,
            )
        if row.artifact_key and row.artifact_size and row.artifact_sha256:
            return Artifact(row.artifact_key, row.artifact_size, row.artifact_sha256)
    return None


def _matches_size(artifact: Artifact) -> bool:
    return object_size(get_settings().MINIO_BUCKET_VIDEOS, artifact.key) == artifact.size


def _verify_body(artifact: Artifact, deadline: float, lost, output=None) -> bool:
    digest = sha256()
    size = 0
    with closing(iter_object_chunks(get_settings().MINIO_BUCKET_VIDEOS, artifact.key)) as chunks:
        for chunk in chunks:
            check_deadline(deadline, lost)
            size += len(chunk)
            if size > artifact.size:
                return False
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    return size == artifact.size and digest.hexdigest() == artifact.digest


def recover(
    lease: ExecutionLease, destination: Path, duration: int, resolution: str | None, deadline: float, lost
) -> Artifact | None:
    check_deadline(deadline, lost)
    artifact = _checkpoint(lease)
    if artifact is None or not 0 < artifact.size <= MAX_VIDEO_BYTES:
        return None
    try:
        if not _matches_size(artifact):
            return None
        ensure_space(destination.parent, artifact.size)
        with destination.open("xb") as output:
            if not _verify_body(artifact, deadline, lost, output):
                return None
        # A checkpoint from 007 proves bytes, but the bytes may predate media validation.
        validate_output(destination, duration, resolution, deadline, lost)
    except (BotoCoreError, ClientError):
        raise MediaError("OBJECT_READ_FAILED", recoverable=True) from None
    except MediaError as error:
        if error.retry:
            raise
        logger.warning("media_checkpoint_rejected work_id=%s reason=%s", lease.work_id, error.reason)
        return None
    return artifact


class UploadStream(BufferedReader):
    """Stop SDK reads, including retries, as soon as this execution must stop."""

    def __init__(self, path: Path, deadline: float, lost):
        super().__init__(path.open("rb", buffering=0))
        self.deadline = deadline
        self.lost = lost

    def read(self, size=-1):
        check_deadline(self.deadline, self.lost)
        return super().read(size)

    def readinto(self, buffer):
        check_deadline(self.deadline, self.lost)
        return super().readinto(buffer)


def upload(lease: ExecutionLease, key: str, path: Path, lost, *, deadline: float | None = None) -> Artifact:
    if deadline is None:
        deadline = monotonic() + 180
    check_deadline(deadline, lost)
    size = path.stat().st_size
    if not 0 < size <= MAX_VIDEO_BYTES:
        raise MediaError("OUTPUT_SIZE")
    digest = sha256()
    try:
        with UploadStream(path, deadline, lost) as stream:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                check_deadline(deadline, lost)
                digest.update(chunk)
            artifact = Artifact(key, size, digest.hexdigest())
            _checkpoint(lease, artifact)
            check_deadline(deadline, lost)
            stream.seek(0)
            upload_file(get_settings().MINIO_BUCKET_VIDEOS, key, stream, size, "video/mp4")
        check_deadline(deadline, lost)
        if not _matches_size(artifact) or not _verify_body(artifact, deadline, lost):
            raise MediaError("OBJECT_INTEGRITY_FAILED", recoverable=True)
    except (BotoCoreError, ClientError):
        raise MediaError("OBJECT_UPLOAD_FAILED", recoverable=True) from None
    return artifact
