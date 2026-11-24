"""S3 兼容对象存储封装（本地指向 MinIO，云端只换 endpoint）。"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO
from urllib.parse import quote, urlparse, urlunparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_OBJECT_MISSING = {"NoSuchKey", "NoSuchVersion"}
_NOT_FOUND_CODES = {"404", "NoSuchBucket", "NotFound", *_OBJECT_MISSING}

# boto3 客户端创建成本很高（要加载 botocore 数据模型，单次 50~150ms），
# 高并发下载/提交时每次新建会直接把 P95 拉爆，这里做成进程级单例（boto3 Client 本身线程安全）。
_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _client():
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = new_s3_client(
                    settings.minio_endpoint_url,
                    settings.MINIO_REGION,
                    settings.MINIO_ACCESS_KEY,
                    settings.MINIO_SECRET_KEY,
                )
    return _CLIENT


def new_s3_client(endpoint: str, region: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            max_pool_connections=50,
            connect_timeout=4,
            read_timeout=15,
            retries={"total_max_attempts": 3, "mode": "standard"},
        ),
    )


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    size: int
    modified: datetime
    etag: str | None = None
    version_id: str | None = None


class StorageUnavailable(RuntimeError):
    """A transport, permission, or bucket error makes the result inconclusive."""


class ObjectConflict(RuntimeError):
    """The destination already contains different bytes."""


def _head(client, bucket: str, key: str) -> ObjectMetadata | None:
    try:
        info = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in _OBJECT_MISSING:
            return None
        if code in {"404", "NotFound"}:
            # HEAD errors from S3 can omit the error body. Confirm the bucket
            # before treating an ambiguous 404 as a missing key.
            client.head_bucket(Bucket=bucket)
            return None
        raise
    return ObjectMetadata(
        key, info["ContentLength"], info["LastModified"], info.get("ETag"), info.get("VersionId")
    )


class ObjectStore:
    """Operations used by reconciliation and by either side of an object transfer."""

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        self.client = client if client is not None else _client()

    @contextmanager
    def operation(self, name: str):
        try:
            yield
        except (ClientError, BotoCoreError) as error:
            code = (
                str(error.response.get("Error", {}).get("Code", "")) if isinstance(error, ClientError) else ""
            )
            # Only stable diagnostic classes cross this boundary.
            reason = (
                code
                if code in {"AccessDenied", "403", "NoSuchBucket", "404", "500", "503", "SlowDown"}
                else type(error).__name__
            )
            raise StorageUnavailable(f"{name}:{reason}") from None

    def head(self, key: str) -> ObjectMetadata | None:
        with self.operation("HEAD"):
            return _head(self.client, self.bucket, key)

    def page(self, limit: int, token: str | None = None):
        options = {"ContinuationToken": token} if token else {}
        with self.operation("LIST"):
            response = self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=limit, **options)
        next_token = response.get("NextContinuationToken")
        if response.get("IsTruncated") and (not next_token or next_token == token):
            raise StorageUnavailable("LIST:invalid_continuation")
        return [
            ObjectMetadata(item["Key"], item["Size"], item["LastModified"], item.get("ETag"))
            for item in response.get("Contents", [])
        ], next_token

    def chunks(self, key: str):
        with self.operation("GET"):
            yield from _chunks(self.client, self.bucket, key)

    def put_new(self, key: str, stream: BinaryIO, size: int, content_type: str) -> bool:
        """Write atomically if absent. False means an existing object won the race."""
        with self.operation("PUT_IF_ABSENT"):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=stream,
                    ContentLength=size,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                    return False
                raise
        return True


def ensure_bucket(bucket: str) -> str:
    """桶存在则返回 exists，不存在则创建并返回 created。"""
    client = _client()
    try:
        client.head_bucket(Bucket=bucket)
        return "exists"
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            try:
                client.create_bucket(Bucket=bucket)
                logger.info("已创建存储桶：%s", bucket)
                return "created"
            except ClientError as create_exc:
                # 多 worker 同时启动时会有并发建桶：另一个进程已建好，视同 exists
                create_code = str(create_exc.response.get("Error", {}).get("Code", ""))
                if create_code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    return "exists"
                raise
        raise


def ensure_buckets() -> dict[str, str]:
    """确保 i2v-public 与 i2v-videos 两个桶就绪。"""
    result: dict[str, str] = {}
    for bucket in (settings.MINIO_BUCKET_PUBLIC, settings.MINIO_BUCKET_VIDEOS):
        result[bucket] = ensure_bucket(bucket)
    return result


def list_buckets() -> list[str]:
    client = _client()
    try:
        return [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    except (ClientError, BotoCoreError):
        logger.exception("列举存储桶失败")
        return []


def presigned_get_url(
    bucket: str,
    key: str,
    expires_in: int = 7200,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """签发临时只读链接。

    - `filename` 非空时通过 `ResponseContentDisposition` 强制浏览器另存为指定文件名
      （RFC 5987：`filename*=UTF-8''...`，中文不乱码）；
    - 配置了 `CDN_BASE_URL` 时把签名 URL 的 host 换成 CDN 域名（签名参数保持不变）。
    """
    client = _client()
    params: dict[str, str] = {"Bucket": bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = content_disposition(filename)
    if content_type:
        params["ResponseContentType"] = content_type

    url = client.generate_presigned_url("get_object", Params=params, ExpiresIn=int(expires_in))
    return apply_cdn_host(url)


def content_disposition(filename: str) -> str:
    """构造兼容中英文的 Content-Disposition（ASCII 回退 + RFC 5987 UTF-8）。"""
    quoted = quote(filename, safe="")
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "download.mp4"
    ascii_fallback = ascii_fallback.replace('"', "")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


def apply_cdn_host(url: str) -> str:
    """把签名 URL 的 scheme+netloc 替换为 CDN_BASE_URL（未配置则原样返回）。"""
    base = (settings.CDN_BASE_URL or "").strip().rstrip("/")
    if not base:
        return url
    parsed = urlparse(url)
    cdn = urlparse(base if "://" in base else f"https://{base}")
    return urlunparse((cdn.scheme, cdn.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def upload_bytes(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    client = _client()
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    return key


def upload_file(
    bucket: str, key: str, stream: BinaryIO, size: int, content_type: str = "application/octet-stream"
) -> None:
    _client().put_object(Bucket=bucket, Key=key, Body=stream, ContentLength=size, ContentType=content_type)


def object_exists(bucket: str, key: str) -> bool:
    return _head(_client(), bucket, key) is not None


def object_size(bucket: str, key: str) -> int | None:
    metadata = _head(_client(), bucket, key)
    return metadata.size if metadata is not None else None


def _chunks(client, bucket: str, key: str, chunk_size: int = 256 * 1024):
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        for chunk in body.iter_chunks(chunk_size):
            if chunk:
                yield chunk
    finally:
        body.close()


def iter_object_chunks(bucket: str, key: str, chunk_size: int = 256 * 1024):
    """Stream the object without buffering it for the ZIP or worker paths."""
    yield from _chunks(_client(), bucket, key, chunk_size)
