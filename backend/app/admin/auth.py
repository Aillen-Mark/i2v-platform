"""管理端认证：与考生端完全隔离的 Cookie 与会话。

admins 表没有 failed_login_count / locked_until 字段（schema.sql 已定），
因此管理端登录保护用 Redis 计数器实现：同一用户名连续失败 5 次锁定 5 分钟。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.core.redis import redis_client
from app.core.security import verify_password
from app.core.sessions import ADMIN_COOKIE, clear_session_cookie, create_session, destroy_session, set_session_cookie
from app.core.timeutil import utcnow
from app.models import Admin
from app.schemas.auth import AdminLoginIn, AdminMeOut, LogoutOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/auth", tags=["管理端 · 认证"])

settings = get_settings()

_FAIL_KEY = "i2v:login:fail:admin:{username}"
_LOCK_KEY = "i2v:login:lock:admin:{username}"


async def _lock_ttl(username: str) -> int:
    ttl = await redis_client.ttl(_LOCK_KEY.format(username=username))
    return int(ttl) if ttl and ttl > 0 else 0


async def _register_failure(username: str) -> int:
    """记录一次失败，返回当前连续失败次数；达到阈值则加锁。"""
    fail_key = _FAIL_KEY.format(username=username)
    count = await redis_client.incr(fail_key)
    await redis_client.expire(fail_key, settings.LOGIN_LOCK_MINUTES * 60)
    if count >= settings.LOGIN_MAX_FAILURES:
        await redis_client.set(_LOCK_KEY.format(username=username), "1", ex=settings.LOGIN_LOCK_MINUTES * 60)
    return int(count)


async def _clear_failures(username: str) -> None:
    await redis_client.delete(_FAIL_KEY.format(username=username), _LOCK_KEY.format(username=username))


@router.post("/login", response_model=AdminMeOut, summary="管理员登录")
async def admin_login(
    payload: AdminLoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AdminMeOut:
    username = payload.username.strip()

    remain = await _lock_ttl(username)
    if remain > 0:
        raise AppError(
            "ACCOUNT_LOCKED",
            f"连续输错次数过多，账号已锁定，请 {remain} 秒后再试",
            status_code=429,
            detail={"retry_after": remain},
        )

    admin = (await session.execute(select(Admin).where(Admin.username == username))).scalar_one_or_none()
    password_ok = admin is not None and await asyncio.to_thread(
        verify_password, payload.password, admin.password_hash
    )

    if not password_ok:
        count = await _register_failure(username)
        if count >= settings.LOGIN_MAX_FAILURES:
            raise AppError(
                "ACCOUNT_LOCKED",
                f"连续输错 {settings.LOGIN_MAX_FAILURES} 次，账号已锁定 {settings.LOGIN_LOCK_MINUTES} 分钟",
                status_code=429,
                detail={"retry_after": settings.LOGIN_LOCK_MINUTES * 60},
            )
        left = settings.LOGIN_MAX_FAILURES - count
        raise AppError(
            "INVALID_CREDENTIALS",
            f"账号或密码错误，还可尝试 {left} 次",
            status_code=401,
            detail={"remaining_attempts": left},
        )

    assert admin is not None
    if admin.status != 1:
        raise AppError("ACCOUNT_DISABLED", "管理员账号已被禁用", status_code=403)

    await _clear_failures(username)
    admin.last_login_at = utcnow()
    await session.commit()

    sid = await create_session("admin", admin.id)
    set_session_cookie(response, "admin", sid)
    logger.info("管理员 %s 登录成功，IP=%s", admin.username, client_ip(request))

    return AdminMeOut(
        id=admin.id,
        username=admin.username,
        role=admin.role,
        last_login_at=admin.last_login_at,
    )


@router.post("/logout", response_model=LogoutOut, summary="管理员退出登录")
async def admin_logout(request: Request, response: Response) -> LogoutOut:
    await destroy_session("admin", request.cookies.get(ADMIN_COOKIE))
    clear_session_cookie(response, "admin")
    return LogoutOut()


@router.get("/me", response_model=AdminMeOut, summary="当前管理员")
async def admin_me(admin: Admin = Depends(get_current_admin)) -> AdminMeOut:
    return AdminMeOut(
        id=admin.id,
        username=admin.username,
        role=admin.role,
        last_login_at=admin.last_login_at,
    )
