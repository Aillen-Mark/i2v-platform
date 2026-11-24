"""Prepare the requested first-frame ratio and produce a playable MP4."""

import base64
import logging
from pathlib import Path
from hashlib import sha256
from time import monotonic

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.storage import iter_object_chunks
from worker.config import worker_settings
from worker.minimax import GenerationError
from worker.media_process import MediaError, check_deadline, run_media
from worker.media_validation import (
    MAX_VIDEO_BYTES,
    PROBE_SECONDS,
    probe_media,
    validate_output,
    validate_video,
)
from worker.media_workspace import ensure_space

logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 100_000_000
MAX_IMAGE_BYTES = 15_000_000
DOWNLOAD_SECONDS = 240
TRANSCODE_SECONDS = 240
FRAME_SIZES = {
    "16:9": (1152, 648),
    "9:16": (648, 1152),
    "1:1": (768, 768),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise GenerationError("UPSTREAM_TIMEOUT")
    return remaining


def prepare_first_frame(image_key: str, ratio: str, deadline: float, directory: Path, lost) -> str:
    if ratio not in FRAME_SIZES:
        raise GenerationError("INVALID_IMAGE")
    ensure_space(directory, MAX_IMAGE_BYTES)
    source = directory / "first-frame"
    try:
        with source.open("xb") as output:
            size = 0
            for chunk in iter_object_chunks(get_settings().MINIO_BUCKET_PUBLIC, image_key):
                check_deadline(deadline, lost)
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise GenerationError("INVALID_IMAGE")
                output.write(chunk)
    except (BotoCoreError, ClientError):
        raise GenerationError(retry="resume") from None

    width, height = FRAME_SIZES[ratio]
    filters = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    try:
        jpeg = run_media(
            [
                worker_settings().I2V_FFMPEG,
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-threads",
                "2",
                "-i",
                str(source),
                "-vf",
                filters,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "pipe:1",
            ],
            "FIRST_FRAME",
            min(deadline, monotonic() + PROBE_SECONDS),
            lost,
            max_output=5_000_000,
        )
    except MediaError as error:
        if error.reason == "FIRST_FRAME_EXIT" and not error.retry:
            raise GenerationError("INVALID_IMAGE") from None
        raise
    if not jpeg:
        raise GenerationError("INVALID_IMAGE")
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def download_video(client: httpx.Client, url: str, source: Path, deadline: float, lost=None) -> None:
    deadline = min(deadline, monotonic() + DOWNLOAD_SECONDS)
    remaining = check_deadline(deadline, lost)
    timeout = httpx.Timeout(min(20, remaining), connect=min(5, remaining))
    digest = sha256()
    size = 0
    try:
        with client.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=timeout,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            if (
                response.status_code != 200
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"
            ):
                raise MediaError("DOWNLOAD_RESPONSE", recoverable=True)
            content_length = response.headers.get("Content-Length")
            expected = int(content_length) if content_length is not None else None
            transfer_modes = [
                mode.strip().lower() for mode in response.headers.get("Transfer-Encoding", "").split(",")
            ]
            if (
                expected is None
                and "chunked" not in transfer_modes
                and response.http_version not in {"HTTP/2", "HTTP/3"}
            ):
                raise MediaError("DOWNLOAD_UNFRAMED", recoverable=True)
            if expected is not None and not 0 < expected <= MAX_SOURCE_BYTES:
                raise MediaError("SOURCE_SIZE")
            ensure_space(source.parent, (expected or MAX_SOURCE_BYTES) + MAX_VIDEO_BYTES)
            with source.open("xb") as stream:
                for chunk in response.iter_raw():
                    check_deadline(deadline, lost)
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise MediaError("SOURCE_SIZE")
                    stream.write(chunk)
                    digest.update(chunk)
            check_deadline(deadline, lost)
            if size == 0 or (expected is not None and expected != size):
                raise MediaError("DOWNLOAD_INCOMPLETE", recoverable=True)
    except (httpx.HTTPError, ValueError):
        raise MediaError("DOWNLOAD_INTERRUPTED", recoverable=True) from None
    logger.info("media_source_downloaded source_bytes=%s source_sha256=%s", size, digest.hexdigest())


def prepare_video(client, url, directory, duration, deadline, lost=None, *, resolution=None) -> Path:
    source = directory / "source.mp4"
    download_video(client, url, source, deadline, lost)
    return transcode(source, directory / "output.mp4", duration, deadline, lost, resolution=resolution)


def transcode(
    source: Path, output: Path, duration: int, deadline: float, lost=None, *, resolution=None
) -> Path:
    ensure_space(source.parent, MAX_VIDEO_BYTES)
    info = probe_media(source, min(deadline, monotonic() + PROBE_SECONDS), lost)
    try:
        validate_video(info, duration, resolution)
    except MediaError as error:
        logger.warning(
            "media_source_rejected reason=%s expected_seconds=%s actual_seconds=%.4f",
            error.reason,
            duration,
            info.duration,
        )
        raise

    arguments = [
        worker_settings().I2V_FFMPEG,
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-y",
        "-err_detect",
        "explode",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file",
        "-threads",
        "2",
        "-i",
        str(source),
    ]
    if info.audio is None:
        arguments += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={info.duration:.6f}",
        ]
        audio_input = "1:a:0"
    else:
        audio_input = f"0:{info.audio.index}"

    max_video_rate = int(MAX_VIDEO_BYTES * 8 / duration * 0.9) - 128_000
    arguments += [
        "-map",
        f"0:{info.video.index}",
        "-map",
        audio_input,
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v",
        "libx264",
        "-threads:v",
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
        "-ac",
        "2",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-movflags",
        "+faststart",
        "-fs",
        str(MAX_VIDEO_BYTES + 1),
        "-f",
        "mp4",
        str(output),
    ]
    start = monotonic()
    run_media(arguments, "TRANSCODE", min(deadline, monotonic() + TRANSCODE_SECONDS), lost)
    transcoded_in = monotonic() - start
    checked = validate_output(output, duration, resolution, deadline, lost)
    logger.info(
        "media_validated source_bytes=%s output_bytes=%s transcode_seconds=%.3f actual_seconds=%.4f width=%s height=%s",
        source.stat().st_size,
        output.stat().st_size,
        transcoded_in,
        checked.duration,
        checked.video.width,
        checked.video.height,
    )
    return output
