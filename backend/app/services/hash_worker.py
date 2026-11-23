"""子进程哈希工作单元：纯函数，供 `concurrent.futures.ProcessPoolExecutor` 调用。

约束（Windows spawn 模式）：
- 本模块**只**依赖 bcrypt 与 cryptography，绝不导入 FastAPI / SQLAlchemy / app.core.config，
  否则 16 个子进程会各自重复加载整个应用，光启动就要好几秒；
- 所有参数显式传入（bcrypt cost、Fernet 密钥），子进程不读全局状态；
- 顶层函数、可 pickle、无副作用，符合 spawn 的 `__main__` 保护要求。
"""

from __future__ import annotations

import bcrypt
from cryptography.fernet import Fernet


def hash_and_encrypt_batch(
    passwords: list[str],
    rounds: int,
    fernet_key: str,
) -> list[tuple[str, str]]:
    """一批密码 → [(bcrypt 哈希, Fernet 密文)]，两者在同一子进程内一次算完。"""
    fernet = Fernet(fernet_key.encode("utf-8"))
    results: list[tuple[str, str]] = []
    for password in passwords:
        raw = password.encode("utf-8")
        digest = bcrypt.hashpw(raw, bcrypt.gensalt(rounds=int(rounds))).decode("utf-8")
        token = fernet.encrypt(raw).decode("utf-8")
        results.append((digest, token))
    return results


def ping() -> str:
    """预热探针：提交一次即让进程池把全部子进程拉起来，避免首次导入时才付启动开销。"""
    return "pong"
