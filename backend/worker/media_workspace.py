"""Lease-specific scratch directories and conservative orphan cleanup."""

import logging
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir
from time import time
from uuid import uuid4

from app.core.database import SyncSessionLocal
from app.models import GenerationExecution
from app.services.execution_lease import database_now, is_live
from worker.config import worker_settings
from worker.media_process import MediaError

if os.name == "nt":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)
DIRECTORY = re.compile(r"w([0-9]+)-a([0-9]+)-g([0-9]+)-[0-9a-f]{32}")
ORPHAN_GRACE = 2 * 60 * 60
DISK_RESERVE = 256 * 1024 * 1024


def scratch_root() -> Path:
    configured = worker_settings().I2V_MEDIA_TMP_DIR
    return Path(configured) if configured else Path(gettempdir()) / "i2v-media"


def ensure_space(directory: Path, required: int) -> None:
    if shutil.disk_usage(directory).free < required + DISK_RESERVE:
        raise MediaError("MEDIA_DISK_SPACE", recoverable=True)


@contextmanager
def _workspace_lock(path: Path, *, create: bool):
    marker = path / ".owner"
    with marker.open("x+b" if create else "r+b") as handle:
        if create:
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            yield True


def _safe_directory(path: Path) -> bool:
    return (
        path.is_dir()
        and not path.is_symlink()
        and not (
            getattr(path.lstat(), "st_file_attributes", 0) & 0x400  # Windows reparse point
        )
    )


def _remove(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("media_cleanup_failed workdir=%s errno=%s", path.name, exc.errno)


@contextmanager
def workspace(lease, attempt_id: int):
    root = scratch_root()
    path = root / f"w{lease.work_id}-a{attempt_id}-g{lease.generation}-{uuid4().hex}"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not _safe_directory(root):
            raise MediaError("MEDIA_UNSAFE_TEMP_ROOT")
        path.mkdir(mode=0o700)
    except OSError:
        raise MediaError("MEDIA_TEMP_CREATE", recoverable=True) from None
    try:
        with _workspace_lock(path, create=True) as acquired:
            if not acquired:
                raise MediaError("MEDIA_TEMP_LOCK", recoverable=True)
            yield path
    except OSError as exc:
        logger.warning("media_temp_io_failed work_id=%s errno=%s", lease.work_id, exc.errno)
        raise MediaError("MEDIA_TEMP_IO", recoverable=True) from None
    finally:
        _remove(path)


def cleanup_orphans() -> None:
    root = scratch_root()
    if not _safe_directory(root):
        return
    cutoff = time() - ORPHAN_GRACE
    for path in root.iterdir():
        match = DIRECTORY.fullmatch(path.name)
        try:
            if not match or not _safe_directory(path) or path.stat().st_mtime > cutoff:
                continue
            work_id, _, generation = map(int, match.groups())
            with _workspace_lock(path, create=False) as acquired:
                if not acquired:
                    continue
                with SyncSessionLocal() as session:
                    row = session.get(GenerationExecution, work_id)
                    if row is None or row.lease_generation < generation:
                        continue
                    if row.lease_generation == generation and is_live(row, database_now(session)):
                        continue
            # A new owner gets a new UUID directory, even for the same lease generation.
            _remove(path)
            logger.info("media_orphan_cleanup work_id=%s lease_generation=%s", work_id, generation)
        except FileNotFoundError:
            # Hard kill may happen after mkdir and before creating the marker. It
            # cannot be in use after the grace period if its generation has ended.
            if match and path.is_dir() and not (path / ".owner").exists():
                work_id, _, generation = map(int, match.groups())
                with SyncSessionLocal() as session:
                    row = session.get(GenerationExecution, work_id)
                    if row and (
                        row.lease_generation > generation
                        or (row.lease_generation == generation and not is_live(row, database_now(session)))
                    ):
                        try:
                            path.rmdir()
                        except OSError as exc:
                            logger.warning("media_cleanup_failed workdir=%s errno=%s", path.name, exc.errno)
        except OSError as exc:
            logger.warning("media_cleanup_failed workdir=%s errno=%s", path.name, exc.errno)
