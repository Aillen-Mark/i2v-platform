"""Redis 单点会话：考生端 `cand_sid` 与管理员端 `admin_sid` 物理隔离。

键约定（M2 手册 §Step 1）：
- `i2v:session:{role}:{sid}`        → user_id（TTL 86400s）
- `i2v:active_sid:{role}:{user_id}` → 当前有效 sid（TTL 86400s）

登录时先删掉该账号的旧 sid 再写入新 sid，实现「同账号后登录踢掉前一个」；
校验时同时比对 active_sid 指针，即使旧 key 因异常残留也不会被接受。
"""

from __future__ import annotations

import logging
import secrets
from typing import Literal

from fastapi import Response

from app.core.config import get_settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

Role = Literal["candidate", "admin"]

CANDIDATE_COOKIE = "cand_sid"
ADMIN_COOKIE = "admin_sid"

COOKIE_NAME: dict[str, str] = {"candidate": CANDIDATE_COOKIE, "admin": ADMIN_COOKIE}


def _session_key(role: str, sid: str) -> str:
    return f"i2v:session:{role}:{sid}"


def _active_key(role: str, user_id: int) -> str:
    return f"i2v:active_sid:{role}:{user_id}"


def new_session_id() -> str:
    """32 字节随机 hex（64 字符）。"""
    return secrets.token_hex(32)


def _ttl() -> int:
    return int(get_settings().SESSION_TTL_SECONDS)


async def create_session(role: Role, user_id: int) -> str:
    """建立新会话并踢掉该账号的旧会话，返回新 sid。

    高并发登录时这里的 3 次 Redis 往返会累积成可观延迟，因此用 pipeline 合并成 2 次。
    """
    ttl = _ttl()
    sid = new_session_id()
    pointer = _active_key(role, int(user_id))

    old_sid = await redis_client.get(pointer)
    pipe = redis_client.pipeline(transaction=False)
    if old_sid and old_sid != sid:
        pipe.delete(_session_key(role, old_sid))
        logger.info("%s %s 的旧会话已被新登录踢下线", role, user_id)
    pipe.set(_session_key(role, sid), str(int(user_id)), ex=ttl)
    pipe.set(pointer, sid, ex=ttl)
    await pipe.execute()
    return sid


async def load_session(role: Role, sid: str | None) -> dict[str, int | str] | None:
    """读取会话；sid 为空、已失效、或被新登录踢掉时返回 None。"""
    if not sid:
        return None
    raw = await redis_client.get(_session_key(role, sid))
    if not raw:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        logger.warning("会话内容损坏，按失效处理：role=%s", role)
        await redis_client.delete(_session_key(role, sid))
        return None

    if await redis_client.get(_active_key(role, user_id)) != sid:
        # 已被同账号的后一次登录顶替
        await redis_client.delete(_session_key(role, sid))
        return None
    return {"user_id": user_id, "role": role}


async def destroy_session(role: Role, sid: str | None) -> None:
    """退出登录：删除会话体；若 active_sid 指针仍指向自己则一并清理。"""
    if not sid:
        return
    raw = await redis_client.get(_session_key(role, sid))
    await redis_client.delete(_session_key(role, sid))
    if not raw:
        return
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return
    pointer = _active_key(role, user_id)
    if await redis_client.get(pointer) == sid:
        await redis_client.delete(pointer)


async def destroy_user_sessions(role: Role, user_ids: list[int]) -> int:
    """强制下线：清掉这些账号的当前会话与指针，返回被清理的会话数。

    用于「批量禁用考生」与「重置密码」——管理员操作后旧 Cookie 必须立刻失效。
    """
    if not user_ids:
        return 0
    pipe = redis_client.pipeline(transaction=False)
    for user_id in user_ids:
        pipe.get(_active_key(role, int(user_id)))
    pointers = await pipe.execute()

    pipe = redis_client.pipeline(transaction=False)
    removed = 0
    for user_id, sid in zip(user_ids, pointers):
        if sid:
            pipe.delete(_session_key(role, sid))
            removed += 1
        pipe.delete(_active_key(role, int(user_id)))
    await pipe.execute()
    if removed:
        logger.info("强制下线 %s 端 %s 个会话", role, removed)
    return removed


def set_session_cookie(response: Response, role: Role, sid: str) -> None:
    """HttpOnly + SameSite=Lax + Max-Age=86400（CLAUDE.md 铁律 6）。"""
    response.set_cookie(
        key=COOKIE_NAME[role],
        value=sid,
        max_age=_ttl(),
        httponly=True,
        samesite="lax",
        path="/",
        secure=get_settings().is_production,
    )


def clear_session_cookie(response: Response, role: Role) -> None:
    response.delete_cookie(
        key=COOKIE_NAME[role],
        path="/",
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
    )
