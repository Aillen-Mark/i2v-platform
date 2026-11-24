"""Media acceptance tests use real FFmpeg and FFprobe."""

import os
from shutil import which
from subprocess import run
from time import monotonic

import pytest

from worker.config import worker_settings
from worker.media import transcode
from worker.media_process import MediaError
from worker.media_validation import MAX_VIDEO_BYTES, validate_output


@pytest.fixture(scope="session")
def media_tools():
    worker_settings.cache_clear()
    settings = worker_settings()
    if not (which(settings.I2V_FFMPEG) and which(settings.I2V_FFPROBE)):
        pytest.skip("Set I2V_FFMPEG and I2V_FFPROBE to the local tool binaries")
    return settings


@pytest.fixture(scope="session")
def sample_factory(media_tools, tmp_path_factory):
    directory = tmp_path_factory.mktemp("samples")

    def make(*, length=15, audio=True, video=True, codec="libx264", ending="mp4", dimensions="640x480"):
        path = directory / f"{length}-{audio}-{video}-{codec}-{dimensions}.{ending}"
        if path.exists():
            return path
        command = [media_tools.I2V_FFMPEG, "-y", "-nostdin", "-v", "error"]
        if video:
            command += ["-f", "lavfi", "-i", f"color=c=blue:s={dimensions}:r=12:d={length}"]
        if audio:
            command += ["-f", "lavfi", "-i", f"sine=f=440:r=48000:d={length}"]
        if video:
            command += ["-c:v", codec, "-threads:v", "2"]
        if audio:
            command += ["-c:a", "aac"]
        run([*command, str(path)], check=True, timeout=45)
        return path

    return make


@pytest.mark.parametrize("audio,codec", [(True, "libx264"), (False, "libx264"), (True, "mpeg4")])
def test_accept_valid_video_and_supply_aac(sample_factory, tmp_path, audio, codec):
    output = transcode(
        sample_factory(audio=audio, codec=codec),
        tmp_path / "output.mp4",
        15,
        monotonic() + 60,
        resolution="480P",
    )
    info = validate_output(output, 15, "480P", monotonic() + 30)
    assert info.video.codec == "h264" and info.audio.codec == "aac" and "mp4" in info.formats
    assert abs(info.duration - 15) < 0.1 and abs(info.audio.duration - info.duration) < 0.1
    if not audio:
        settings = worker_settings()
        silence = run(
            [
                settings.I2V_FFMPEG,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(output),
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "-t",
                "0.25",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        assert silence and not any(silence)


@pytest.mark.parametrize("audio", [True, False])
def test_reject_one_second_for_fifteen_second_task(sample_factory, tmp_path, audio):
    with pytest.raises(MediaError, match="MEDIA_FAILED") as error:
        transcode(sample_factory(length=1, audio=audio), tmp_path / "output.mp4", 15, monotonic() + 20)
    assert error.value.reason == "VIDEO_DURATION_MISMATCH"


@pytest.mark.parametrize("length,accepted", [(14.7, True), (14.2, False), (15.8, False), (5, True)])
def test_uses_work_duration_and_accepts_small_tolerance(sample_factory, tmp_path, length, accepted):
    def encode():
        return transcode(
            sample_factory(length=length), tmp_path / "output.mp4", 5 if length == 5 else 15, monotonic() + 35
        )

    if accepted:
        assert encode().is_file()
    else:
        with pytest.raises(MediaError) as error:
            encode()
        assert error.value.reason == "VIDEO_DURATION_MISMATCH"


def test_reject_missing_video_and_invalid_files(sample_factory, tmp_path):
    audio_only = sample_factory(video=False, length=1)
    damage = tmp_path / "broken.mp4"
    damage.write_bytes(b"not an MP4")
    empty = tmp_path / "empty.mp4"
    empty.touch()
    for path, reason in [(audio_only, "NO_VIDEO_STREAM"), (damage, "FFPROBE_EXIT"), (empty, "FFPROBE_EXIT")]:
        with pytest.raises(MediaError) as error:
            transcode(path, tmp_path / "output.mp4", 15, monotonic() + 20)
        assert error.value.reason == reason
    with empty.open("wb") as handle:
        handle.truncate(MAX_VIDEO_BYTES + 1)
    with pytest.raises(MediaError) as error:
        validate_output(empty, 15, None, monotonic() + 20)
    assert error.value.reason == "OUTPUT_SIZE"
    assert os.path.getsize(empty) == MAX_VIDEO_BYTES + 1


def test_reject_wrong_resolution_and_final_codec(sample_factory, tmp_path):
    with pytest.raises(MediaError) as error:
        transcode(sample_factory(), tmp_path / "output.mp4", 15, monotonic() + 20, resolution="768P")
    assert error.value.reason == "VIDEO_RESOLUTION_MISMATCH"
    with pytest.raises(MediaError) as error:
        validate_output(sample_factory(codec="mpeg4"), 15, "480P", monotonic() + 20)
    assert error.value.reason == "OUTPUT_VIDEO_FORMAT"


def test_complete_decode_detects_damage_after_mp4_header(sample_factory, tmp_path):
    sample = sample_factory()
    playable = transcode(sample, tmp_path / "faststart.mp4", 15, monotonic() + 45)
    with playable.open("r+b") as stream:
        stream.truncate(playable.stat().st_size * 2 // 3)
    from worker.media_validation import probe_media

    assert abs(probe_media(playable, monotonic() + 15).duration - 15) < 0.1
    with pytest.raises(MediaError) as error:
        validate_output(playable, 15, "480P", monotonic() + 20)
    assert error.value.reason == "DECODE_EXIT" and error.value.retry is None


def test_reject_non_mp4_container(sample_factory, media_tools, tmp_path):
    target = tmp_path / "sample.mov"
    run(
        [media_tools.I2V_FFMPEG, "-v", "error", "-i", str(sample_factory()), "-c", "copy", str(target)],
        check=True,
        timeout=15,
    )
    with pytest.raises(MediaError) as error:
        validate_output(target, 15, "480P", monotonic() + 15)
    assert error.value.reason == "OUTPUT_CONTAINER"


def test_undecodable_audio_is_not_replaced_with_silence(sample_factory, media_tools, tmp_path):
    import json
    from shutil import copyfile

    source = tmp_path / "corrupt-audio.mp4"
    copyfile(sample_factory(), source)
    packets = json.loads(
        run(
            [
                media_tools.I2V_FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_packets",
                "-show_entries",
                "packet=pos,size",
                "-of",
                "json",
                str(source),
            ],
            timeout=15,
            check=True,
            capture_output=True,
        ).stdout
    )["packets"]
    with source.open("r+b") as stream:
        for packet in packets[10:16]:
            stream.seek(int(packet["pos"]))
            stream.write(b"\xff" * int(packet["size"]))
    with pytest.raises(MediaError) as error:
        transcode(source, tmp_path / "output.mp4", 15, monotonic() + 20)
    assert error.value.reason == "TRANSCODE_EXIT"


def test_ffmpeg_timeout_and_nonzero_are_failures(media_tools, tmp_path, caplog):
    from worker.media_process import run_media

    with pytest.raises(MediaError) as error:
        run_media(
            [
                media_tools.I2V_FFMPEG,
                "-v",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "anullsrc",
                "-t",
                "20",
                "-f",
                "null",
                "-",
            ],
            "TRANSCODE",
            monotonic() + 2,
        )
    assert error.value.reason == "TRANSCODE_TIMEOUT"
    assert "media_process_timeout" in caplog.text
    with pytest.raises(MediaError) as error:
        run_media(
            [media_tools.I2V_FFMPEG, "-v", "error", "-i", str(tmp_path / "missing.mp4"), "-f", "null", "-"],
            "TRANSCODE",
            monotonic() + 10,
        )
    assert error.value.reason == "TRANSCODE_EXIT"
    assert "returncode=" in caplog.text


@pytest.fixture
def broken_http():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread

    requested, stopped = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.set()
            if self.path == "/stall":
                stopped.wait(8)
                return
            self.send_response(200)
            if self.path == "/unframed":
                self.end_headers()
                self.wfile.write(b"cannot verify completeness")
                return
            if self.path == "/chunked":
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"4\r\ndata\r\n0\r\n\r\n")
                return
            self.send_header("Content-Length", "200")
            self.end_headers()
            self.wfile.write(b"partial")

        def log_message(self, *_):
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requested
        finally:
            stopped.set()
            server.shutdown()
            thread.join(timeout=2)


def test_real_ffprobe_killed_on_timeout(media_tools, broken_http):
    from worker.media_process import run_media

    url, requested = broken_http
    with pytest.raises(MediaError) as error:
        run_media(
            [media_tools.I2V_FFPROBE, "-v", "error", f"{url}/stall"],
            "FFPROBE",
            monotonic() + 2,
            max_output=1024,
        )
    assert requested.is_set()
    assert error.value.reason == "FFPROBE_TIMEOUT"


def test_interrupted_download_stops_before_transcode(broken_http, tmp_path):
    import httpx
    from worker.media import prepare_video

    url, requested = broken_http
    with httpx.Client(trust_env=False) as client, pytest.raises(MediaError) as error:
        prepare_video(client, url, tmp_path, 15, monotonic() + 10)
    assert requested.is_set()
    assert error.value.reason == "DOWNLOAD_INTERRUPTED"
    assert not (tmp_path / "output.mp4").exists()


def test_disk_reserve_blocks_processing(sample_factory, tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(MediaError) as error:
        transcode(sample_factory(), tmp_path / "output.mp4", 15, monotonic() + 20)
    assert error.value.reason == "MEDIA_DISK_SPACE" and error.value.retry == "resume"
    assert not (tmp_path / "output.mp4").exists()


def test_heartbeat_loss_kills_running_ffmpeg(media_tools, monkeypatch):
    import subprocess
    from threading import Event, Thread
    from time import sleep

    from app.services.execution_lease import ExecutionStopped
    from worker.media_process import run_media

    original = subprocess.Popen
    processes = []
    lost = Event()

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    def interrupt():
        while not processes:
            sleep(0.01)
        sleep(0.8)
        lost.set()

    monkeypatch.setattr(subprocess, "Popen", launch)
    thread = Thread(target=interrupt, daemon=True)
    thread.start()
    with pytest.raises(ExecutionStopped):
        run_media(
            [
                media_tools.I2V_FFMPEG,
                "-v",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "anullsrc",
                "-t",
                "20",
                "-f",
                "null",
                "-",
            ],
            "TRANSCODE",
            monotonic() + 10,
            lost,
        )
    thread.join(timeout=2)
    assert len(processes) == 1 and processes[0].poll() is not None


def test_download_requires_complete_http_framing(broken_http, tmp_path):
    import httpx
    from worker.media import download_video

    url, _ = broken_http
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(MediaError) as error:
            download_video(client, url + "/unframed", tmp_path / "unframed", monotonic() + 10)
        assert error.value.reason == "DOWNLOAD_UNFRAMED"
        download = tmp_path / "chunked"
        download_video(client, url + "/chunked", download, monotonic() + 10)
        assert download.read_bytes() == b"data"
