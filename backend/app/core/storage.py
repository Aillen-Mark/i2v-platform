"""S3 兼容对象存储封装（本地指向 MinIO，云端只换 endpoint）。"""

from __future__ import annotations

import logging
import threading
from typing import BinaryIO
from urllib.parse import quote, urlparse, urlunparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_NOT_FOUND_CODES = {"404", "NoSuchBucket", "NotFound", "NoSuchKey"}

# boto3 客户端创建成本很高（要加载 botocore 数据模型，单次 50~150ms），
# 高并发下载/提交时每次新建会直接把 P95 拉爆，这里做成进程级单例（boto3 Client 本身线程安全）。
_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _client():
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = boto3.client(
                    "s3",
                    endpoint_url=settings.minio_endpoint_url,
                    aws_access_key_id=settings.MINIO_ACCESS_KEY,
                    aws_secret_access_key=settings.MINIO_SECRET_KEY,
                    region_name=settings.MINIO_REGION,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "path"},
                        max_pool_connections=50,
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
    return _CLIENT


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
    client = _client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            return False
        raise


def object_size(bucket: str, key: str) -> int | None:
    """对象字节数；不存在返回 None。"""
    client = _client()
    try:
        return int(client.head_object(Bucket=bucket, Key=key).get("ContentLength") or 0)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            return None
        raise


def iter_object_chunks(bucket: str, key: str, chunk_size: int = 256 * 1024):
    """流式读取对象（边拉边吐，绝不整体读入内存），用于 zip 打包下载。"""
    client = _client()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        for chunk in body.iter_chunks(chunk_size):
            if chunk:
                yield chunk
    finally:
        body.close()
