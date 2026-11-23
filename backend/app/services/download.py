"""成片下载服务：规范文件名（RFC 5987）、签名链接参数、流式 zip 打包。

红线：
- 下载签名有效期严格 1800 秒（30 分钟）；
- zip 打包必须边从 MinIO 读边向客户端吐，绝不把 MP4 整体读进内存；
- 已编码的 MP4 用 `ZIP_STORED`（compress_type=0），不重复压缩、不浪费 CPU。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone

from zipstream import ZipStream

from app.core.config import get_settings
from app.core.security import utcnow
from app.core.storage import iter_object_chunks

logger = logging.getLogger(__name__)

settings = get_settings()

DOWNLOAD_TTL_SECONDS = 1800
SHANGHAI = timezone(timedelta(hours=8))
CHUNK_SIZE = 256 * 1024


def shanghai_stamp(value: datetime | None = None, *, date_only: bool = False) -> str:
    """naive UTC → 上海时间字符串（文件名用的时间戳按东八区展示，符合用户直觉）。"""
    base = (value or utcnow()).replace(tzinfo=timezone.utc).astimezone(SHANGHAI)
    return base.strftime("%Y%m%d" if date_only else "%Y%m%d%H%M%S")


def build_video_filename(
    account: str,
    question_code: str,
    seq: int | None,
    finished_at: datetime | None,
) -> str:
    """规范文件名：`{账号}_{题号}_第{seq}次_{YYYYMMDDHHMMSS}.mp4`。"""
    return f"{account}_{question_code}_第{int(seq or 1)}次_{shanghai_stamp(finished_at)}.mp4"


def build_zip_filename(account: str) -> str:
    return f"{account}_全部作品_{shanghai_stamp(date_only=True)}.zip"


def unique_arcname(name: str, used: set[str]) -> str:
    """同名冲突时追加 (2)(3)…，避免 zip 内互相覆盖。"""
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    index = 2
    while True:
        candidate = f"{stem}({index}){'.' + ext if ext else ''}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def stream_zip(items: Iterable[tuple[str, str]]) -> Iterator[bytes]:
    """把 [(zip 内文件名, MinIO key)] 流式打包成 zip 字节流。"""
    used: set[str] = set()
    archive = ZipStream(compress_type=0, sized=False)
    for arcname, key in items:
        archive.add(
            iter_object_chunks(settings.MINIO_BUCKET_VIDEOS, key, CHUNK_SIZE),
            unique_arcname(arcname, used),
        )
    yield from archive
