"""Bounded FFmpeg and FFprobe subprocess execution."""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Thread
from time import monotonic

from app.services.execution_lease import ExecutionStopped
from worker.minimax import GenerationError

logger = logging.getLogger(__name__)
PROBE_OUTPUT_LIMIT = 256 * 1024


class MediaError(GenerationError):
    def __init__(self, reason: str, *, recoverable: bool = False):
        super().__init__("MEDIA_FAILED", retry="resume" if recoverable else None)
        self.reason = reason


def check_deadline(deadline: float, lost=None) -> float:
    if lost is not None and lost.is_set():
        raise ExecutionStopped("Media execution lost its lease")
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise MediaError("MEDIA_DEADLINE", recoverable=True)
    return remaining


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def run_media(arguments: list[str], stage: str, deadline: float, lost=None, *, max_output: int = 0) -> bytes:
    check_deadline(deadline, lost)
    command = arguments
    if sys.platform.startswith("linux") or os.name == "nt":
        guard = Path(__file__).with_name("media_child.py")
        command = [sys.executable, str(guard), str(os.getpid()), *arguments]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if max_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
    except OSError:
        logger.warning("media_process_start_failed stage=%s", stage)
        raise MediaError(f"{stage}_START", recoverable=True) from None

    captured = []

    def read_output():
        with process.stdout as output:
            captured.append(output.read(max_output + 1))

    reader = None
    try:
        if max_output:
            reader = Thread(target=read_output, daemon=True)
            reader.start()
        while True:
            try:
                wait = min(0.2, check_deadline(deadline, lost))
            except MediaError:
                logger.warning("media_process_timeout stage=%s", stage)
                raise MediaError(f"{stage}_TIMEOUT", recoverable=True) from None
            if captured and len(captured[0]) > max_output:
                logger.warning("media_process_oversize_output stage=%s", stage)
                raise MediaError(f"{stage}_OUTPUT_LIMIT")
            try:
                status = process.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        if status != 0:
            logger.warning("media_process_failed stage=%s returncode=%s", stage, status)
            raise MediaError(
                f"{stage}_EXIT", recoverable=status < 0 or status == 125 or 0xC0000000 <= status < 0xD0000000
            )
        check_deadline(deadline, lost)
    finally:
        _kill(process)
        if reader is not None:
            reader.join(timeout=2)
    if max_output:
        if not captured or len(captured[0]) > max_output:
            raise MediaError(f"{stage}_OUTPUT_LIMIT")
        return captured[0]
    return b""
