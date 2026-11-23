"""Prepare the requested first-frame ratio and produce a playable MP4."""

import base64
import subprocess
from pathlib import Path
from time import time

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.storage import iter_object_chunks
from worker.config import worker_settings
from worker.minimax import GenerationError

MAX_VIDEO_BYTES = 30_000_000
MAX_SOURCE_BYTES = 100_000_000
FRAME_SIZES = {
    "16:9": (1152, 648),
    "9:16": (648, 1152),
    "1:1": (768, 768),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - time()
    if remaining <= 0:
        raise GenerationError("UPSTREAM_TIMEOUT")
    return remaining


def _ffmpeg(arguments: list[str], deadline: float, *, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        [
            worker_settings().I2V_FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-filter_threads",
            "1",
            "-y",
            *arguments,
        ],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=remaining_seconds(deadline),
        check=True,
    )
    return result.stdout


def prepare_first_frame(image_key: str, ratio: str, deadline: float) -> str:
    if ratio not in FRAME_SIZES:
        raise GenerationError("INVALID_IMAGE")
    try:
        data = b"".join(iter_object_chunks(get_settings().MINIO_BUCKET_PUBLIC, image_key))
    except (BotoCoreError, ClientError):
        raise GenerationError(retry="resume") from None

    width, height = FRAME_SIZES[ratio]
    filters = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    try:
        jpeg = _ffmpeg(
            ["-i", "pipe:0", "-vf", filters, "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"],
            deadline,
            data=data,
        )
    except subprocess.CalledProcessError:
        raise GenerationError("INVALID_IMAGE") from None
    except subprocess.TimeoutExpired:
        raise GenerationError("UPSTREAM_TIMEOUT") from None
    except OSError:
        raise GenerationError("MEDIA_FAILED") from None
    if not jpeg:
        raise GenerationError("INVALID_IMAGE")
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def prepare_video(client: httpx.Client, url: str, directory: Path, duration: int, deadline: float) -> bytes:
    source = directory / "source.mp4"
    output = directory / "output.mp4"
    try:
        with client.stream(
            "GET", url, follow_redirects=True, timeout=min(20, remaining_seconds(deadline))
        ) as response:
            response.raise_for_status()
            size = 0
            with source.open("wb") as stream:
                for chunk in response.iter_bytes(128 * 1024):
                    remaining_seconds(deadline)
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise GenerationError("MEDIA_FAILED")
                    stream.write(chunk)
    except (httpx.HTTPError, OSError):
        raise GenerationError("MEDIA_FAILED", retry="resume") from None

    # Estimate a bitrate from the size limit and reserve room for AAC and the MP4 container.
    max_video_rate = int(MAX_VIDEO_BYTES * 8 / duration * 0.9) - 128_000
    try:
        _ffmpeg(
            [
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "21",
                "-maxrate",
                str(max_video_rate),
                "-bufsize",
                str(max_video_rate * 2),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-movflags",
                "+faststart",
                str(output),
            ],
            deadline,
        )
        if not 0 < output.stat().st_size <= MAX_VIDEO_BYTES:
            raise GenerationError("MEDIA_FAILED")
        return output.read_bytes()
    except subprocess.TimeoutExpired:
        raise GenerationError("UPSTREAM_TIMEOUT") from None
    except (subprocess.CalledProcessError, OSError):
        raise GenerationError("MEDIA_FAILED") from None
