"""Recover uploads without allowing stale workers to overwrite a new artifact."""

from dataclasses import dataclass
from hashlib import sha256

from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.database import SyncSessionLocal
from app.core.storage import iter_object_chunks, object_size, upload_bytes
from app.services.execution_lease import ExecutionLease, ExecutionStopped, active_work, check_lease
from worker.minimax import GenerationError


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


def verify(artifact: Artifact) -> bool:
    bucket = get_settings().MINIO_BUCKET_VIDEOS
    try:
        if object_size(bucket, artifact.key) != artifact.size:
            return False
        digest = sha256()
        for chunk in iter_object_chunks(bucket, artifact.key):
            digest.update(chunk)
        return digest.hexdigest() == artifact.digest
    except (BotoCoreError, ClientError):
        raise GenerationError("MEDIA_FAILED", retry="resume") from None


def recover(lease: ExecutionLease) -> Artifact | None:
    artifact = _checkpoint(lease)
    return artifact if artifact is not None and verify(artifact) else None


def upload(lease: ExecutionLease, key: str, data: bytes, lost) -> Artifact:
    artifact = Artifact(key, len(data), sha256(data).hexdigest())
    _checkpoint(lease, artifact)
    if lost.is_set():
        raise ExecutionStopped(lease.work_id)
    try:
        upload_bytes(get_settings().MINIO_BUCKET_VIDEOS, key, data, "video/mp4")
    except (BotoCoreError, ClientError):
        raise GenerationError("MEDIA_FAILED", retry="resume") from None
    if not verify(artifact):
        raise GenerationError("MEDIA_FAILED", retry="resume")
    return artifact
