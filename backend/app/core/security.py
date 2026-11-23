"""安全工具：考试密码生成、bcrypt 哈希校验、Fernet 初始密码加解密。

刻意不使用 passlib：新版 Python 与 bcrypt>=4.1 下 passlib 存在兼容 Bug。
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.timeutil import to_iso_utc, utcnow

logger = logging.getLogger(__name__)

# 需求 4.1：剔除易混字符 O, 0, I, l, 1
AMBIGUOUS_CHARS: str = "O0Il1"

_UPPER: str = "".join(c for c in string.ascii_uppercase if c not in AMBIGUOUS_CHARS)
_LOWER: str = "".join(c for c in string.ascii_lowercase if c not in AMBIGUOUS_CHARS)
_DIGITS: str = "".join(c for c in string.digits if c not in AMBIGUOUS_CHARS)
_ALL: str = _UPPER + _LOWER + _DIGITS


def bcrypt_rounds() -> int:
    """bcrypt cost，来自配置（默认 10，见 decisions.md A7）。"""
    return int(get_settings().BCRYPT_ROUNDS)


def generate_exam_password(length: int = 8) -> str:
    """生成考试初始密码：必含大写、小写、数字，且不含 O/0/I/l/1。"""
    if length < 3:
        raise ValueError("密码长度至少为 3 位（需容纳大写、小写、数字各一位）")
    rng = secrets.SystemRandom()
    while True:
        chars = [rng.choice(_UPPER), rng.choice(_LOWER), rng.choice(_DIGITS)]
        chars.extend(rng.choice(_ALL) for _ in range(length - 3))
        rng.shuffle(chars)
        password = "".join(chars)
        if not any(c in AMBIGUOUS_CHARS for c in password):
            return password


def hash_password(plain_password: str) -> str:
    """bcrypt 加盐哈希（原生 bcrypt.hashpw，不经过 passlib）。"""
    if not plain_password:
        raise ValueError("密码不能为空")
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(rounds=bcrypt_rounds())).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """bcrypt 校验；哈希非法或为空时返回 False，不抛异常。"""
    if not plain_password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        logger.warning("bcrypt 校验失败：哈希格式非法")
        return False


def _fernet() -> Fernet:
    return Fernet(get_settings().AES_FERNET_KEY.encode("utf-8"))


def encrypt_init_pwd(plain_password: str) -> str:
    """初始密码对称加密，供管理员在有效期内查看/导出。"""
    if not plain_password:
        raise ValueError("待加密密码不能为空")
    return _fernet().encrypt(plain_password.encode("utf-8")).decode("utf-8")


def decrypt_init_pwd(token: str) -> str | None:
    """解密初始密码；密钥不匹配或内容被篡改时返回 None。"""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("初始密码解密失败：令牌无效")
        return None


def init_pwd_expire_at(now: datetime | None = None) -> datetime:
    """初始密码可查看截止时间 = 当前 UTC 时间 + INIT_PWD_VIEW_HOURS。"""
    base = now or utcnow()
    return base + timedelta(hours=get_settings().INIT_PWD_VIEW_HOURS)


__all__ = [
    "AMBIGUOUS_CHARS",
    "bcrypt_rounds",
    "decrypt_init_pwd",
    "encrypt_init_pwd",
    "generate_exam_password",
    "hash_password",
    "init_pwd_expire_at",
    "to_iso_utc",
    "utcnow",
    "verify_password",
]
