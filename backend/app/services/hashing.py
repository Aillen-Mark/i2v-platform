"""并行哈希调度：ProcessPoolExecutor 为主、线程池兜底。

为什么用进程池：
- bcrypt cost=10 单次约 56ms，1000 个初始密码串行要 55 秒，直接击穿「1000 行 ≤10 秒」红线；
- Windows 下 spawn 16 个 worker 实测 1000 个哈希约 5.5~6.7 秒（含进程启动），
  线程池虽更快（约 5 秒），但进程池不会与事件循环抢 GIL，导入期间 API 仍可服务其他请求。

进程池在应用启动时预热（见 `app/main.py` lifespan），请求路径上不再付 spawn 开销；
任何异常都降级为线程池，保证导入功能不因进程池故障而失败。
"""

from __future__ import annotations

import asyncio
import logging
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from app.core.config import get_settings
from app.services.hash_worker import hash_and_encrypt_batch, ping

logger = logging.getLogger(__name__)

_POOL: ProcessPoolExecutor | None = None
_POOL_FAILED = False


def pool_workers() -> int:
    """worker 数量：优先取配置，0 表示按 CPU 自动（上限 16，避免过度抢占）。"""
    configured = int(get_settings().HASH_POOL_WORKERS or 0)
    if configured > 0:
        return configured
    return min(16, max(2, os.cpu_count() or 4))


def get_pool() -> ProcessPoolExecutor:
    """惰性创建全局进程池（spawn 上下文，Windows 必需）。"""
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(
            max_workers=pool_workers(),
            mp_context=multiprocessing.get_context("spawn"),
        )
        logger.info("已创建哈希进程池：%s 个 worker（spawn）", pool_workers())
    return _POOL


def shutdown_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None
        logger.info("哈希进程池已关闭")


async def warmup() -> bool:
    """预热：提交一次 ping，让子进程提前就位；失败则标记降级。"""
    global _POOL_FAILED
    if _POOL_FAILED:
        return False
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(loop.run_in_executor(get_pool(), ping), timeout=90)
    except Exception:  # noqa: BLE001 - 预热失败不应阻塞启动
        _POOL_FAILED = True
        logger.exception("哈希进程池预热失败，后续改为线程池执行")
        return False
    return result == "pong"


def _chunk(passwords: list[str], workers: int) -> list[list[str]]:
    """切成 workers×4 份，兼顾负载均衡与 IPC 次数。"""
    parts = max(1, workers * 4)
    size = max(1, math.ceil(len(passwords) / parts))
    return [passwords[i : i + size] for i in range(0, len(passwords), size)]


def _thread_worker(passwords: list[str], rounds: int, fernet_key: str) -> list[tuple[str, str]]:
    return hash_and_encrypt_batch(passwords, rounds, fernet_key)


async def hash_and_encrypt_many(
    passwords: list[str],
    rounds: int,
    fernet_key: str,
) -> list[tuple[str, str]]:
    """并行计算 (bcrypt 哈希, Fernet 密文) 列表，顺序与入参严格一致。

    主路径：ProcessPoolExecutor（spawn，14 worker）分片并行；
    进程池不可用或异常时整体退回线程池（bcrypt 释放 GIL，线程同样能并行）。
    实测：本机 8C/16T 下 1000 个 cost=10 约 6.0~7.0 秒（机器空载），整批导入 5.1~7.6 秒。
    """
    if not passwords:
        return []

    loop = asyncio.get_running_loop()
    workers = pool_workers()
    chunks = _chunk(passwords, workers)

    if not _POOL_FAILED:
        try:
            pool = get_pool()
            parts = await asyncio.gather(
                *[
                    loop.run_in_executor(pool, hash_and_encrypt_batch, chunk, rounds, fernet_key)
                    for chunk in chunks
                ]
            )
            flat = [item for part in parts for item in part]
            if len(flat) != len(passwords):
                raise RuntimeError(f"进程池返回数量不一致：{len(flat)} != {len(passwords)}")
            return flat
        except Exception:  # noqa: BLE001 - 进程池不可用时降级
            logger.exception("进程池哈希失败，降级为线程池")

    with ThreadPoolExecutor(max_workers=workers) as tpool:
        parts = await asyncio.gather(
            *[loop.run_in_executor(tpool, _thread_worker, chunk, rounds, fernet_key) for chunk in chunks]
        )
    return [item for part in parts for item in part]
