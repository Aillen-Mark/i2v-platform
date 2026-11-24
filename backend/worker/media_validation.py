"""Probe and validate the audio, video and container delivered by the worker."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from worker.config import worker_settings
from worker.media_process import PROBE_OUTPUT_LIMIT, MediaError, run_media

MAX_VIDEO_BYTES = 30_000_000
DURATION_TOLERANCE = 0.5
PROBE_SECONDS = 30
DECODE_SECONDS = 120


@dataclass(frozen=True)
class MediaStream:
    index: int
    codec: str
    duration: float | None
    width: int
    height: int
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class MediaInfo:
    video: MediaStream
    audio: MediaStream | None
    duration: float
    formats: frozenset[str]
    brand: str
    stream_count: int


def _duration(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _stream_duration(stream: dict) -> float | None:
    duration = _duration(stream.get("duration"))
    if duration is not None:
        return duration
    try:
        numerator, denominator = stream["time_base"].split("/", 1)
        return _duration(int(stream["duration_ts"]) * int(numerator) / int(denominator))
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return None


def _stream(value: dict) -> MediaStream:
    return MediaStream(
        int(value["index"]),
        value.get("codec_name", ""),
        _stream_duration(value),
        int(value.get("width") or 0),
        int(value.get("height") or 0),
        int(value.get("sample_rate") or 0),
        int(value.get("channels") or 0),
    )


def probe_media(path: Path, deadline: float, lost=None) -> MediaInfo:
    result = run_media(
        [
            worker_settings().I2V_FFPROBE,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,duration,duration_ts,time_base,sample_rate,channels:"
            "stream_disposition=attached_pic:format=format_name,duration:format_tags=major_brand",
            "-of",
            "json",
            str(path),
        ],
        "FFPROBE",
        deadline,
        lost,
        max_output=PROBE_OUTPUT_LIMIT,
    )
    try:
        data = json.loads(result)
        streams = data.get("streams", [])
        format_data = data.get("format", {})
        video = next(
            (
                s
                for s in streams
                if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        audio = next((s for s in streams if s["codec_type"] == "audio"), None)
        if video is None:
            raise MediaError("NO_VIDEO_STREAM")
        video_info = _stream(video)
        duration = video_info.duration
        # A container with multiple streams can inherit the duration of a longer audio track.
        if duration is None and all(s is video for s in streams):
            duration = _duration(format_data.get("duration"))
        if duration is None:
            raise MediaError("UNKNOWN_VIDEO_DURATION")
        return MediaInfo(
            video_info,
            _stream(audio) if audio is not None else None,
            duration,
            frozenset(format_data.get("format_name", "").split(",")),
            str(format_data.get("tags", {}).get("major_brand", "")),
            len(streams),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MediaError("INVALID_PROBE_DATA") from None


def validate_video(info: MediaInfo, expected: int, resolution: str | None) -> None:
    if expected <= 0 or abs(info.duration - expected) > DURATION_TOLERANCE + 1e-6:
        raise MediaError("VIDEO_DURATION_MISMATCH")
    short, long = sorted((info.video.width, info.video.height))
    if short < 16 or long > 4096 or short * long > 4096 * 2160:
        raise MediaError("INVALID_VIDEO_DIMENSIONS")
    tier = {"480P": (384, 540, 1010), "768P": (614, 864, 1614), "2K": (1100, 1536, 3072)}
    if resolution and resolution.upper() in tier:
        minimum, maximum, max_long = tier[resolution.upper()]
        if not minimum <= short <= maximum or long > max_long:
            raise MediaError("VIDEO_RESOLUTION_MISMATCH")


def validate_output(
    path: Path, expected: int, resolution: str | None, deadline: float, lost=None
) -> MediaInfo:
    size = path.stat().st_size
    if not 0 < size <= MAX_VIDEO_BYTES:
        raise MediaError("OUTPUT_SIZE")
    info = probe_media(path, min(deadline, monotonic() + PROBE_SECONDS), lost)
    validate_video(info, expected, resolution)
    video, audio = info.video, info.audio
    if info.stream_count != 2:
        raise MediaError("OUTPUT_STREAM_COUNT")
    if "mp4" not in info.formats or not (
        info.brand.startswith(("iso", "mp4")) or info.brand in {"avc1", "M4V "}
    ):
        raise MediaError("OUTPUT_CONTAINER")
    if video.codec != "h264" or video.width % 2 or video.height % 2:
        raise MediaError("OUTPUT_VIDEO_FORMAT")
    if not audio or audio.codec != "aac" or audio.sample_rate != 48000 or audio.channels not in (1, 2):
        raise MediaError("OUTPUT_AUDIO_FORMAT")
    if audio.duration is None or abs(audio.duration - info.duration) > DURATION_TOLERANCE + 1e-6:
        raise MediaError("OUTPUT_AUDIO_DURATION")
    run_media(
        [
            worker_settings().I2V_FFMPEG,
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-protocol_whitelist",
            "file",
            "-threads",
            "2",
            "-i",
            str(path),
            "-map",
            f"0:{video.index}",
            "-map",
            f"0:{audio.index}",
            "-f",
            "null",
            "-",
        ],
        "DECODE",
        min(deadline, monotonic() + DECODE_SECONDS),
        lost,
    )
    return info
