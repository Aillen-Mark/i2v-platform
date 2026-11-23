"""FastAPI 依赖：考生 / 管理员当前登录用户、客户端 IP。

两端会话完全隔离：`cand_sid` 只能换出 Candidate，`admin_sid` 只能换出 Admin，
拿考生 Cookie 访问 `/admin/*` 必然 401。
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import AppError
from app.core.sessions import ADMIN_COOKIE, CANDIDATE_COOKIE, load_session
from app.models import Admin, Candidate


def client_ip(request: Request) -> str:
    """取客户端 IP：优先 X-Forwarded-For 首个地址（Nginx 反代场景），否则 socket 对端。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:45]
    if request.client and request.client.host:
        return request.client.host[:45]
    return "0.0.0.0"


async def get_current_candidate(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Candidate:
    """当前登录考生；未登录 401，账号被禁用 403。"""
    body = await load_session("candidate", request.cookies.get(CANDIDATE_COOKIE))
    if not body:
        raise AppError("UNAUTHORIZED", "请先登录", status_code=401)

    candidate = await session.get(Candidate, int(body["user_id"]))
    if candidate is None:
        raise AppError("UNAUTHORIZED", "账号不存在或已被删除", status_code=401)
    if candidate.status != 1:
        raise AppError("ACCOUNT_DISABLED", "账号已被禁用，请联系管理员", status_code=403)
    return candidate


async def get_current_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Admin:
    """当前登录管理员；未登录 401，账号被禁用 403。"""
    body = await load_session("admin", request.cookies.get(ADMIN_COOKIE))
    if not body:
        raise AppError("UNAUTHORIZED", "请先登录管理后台", status_code=401)

    admin = await session.get(Admin, int(body["user_id"]))
    if admin is None:
        raise AppError("UNAUTHORIZED", "管理员账号不存在", status_code=401)
    if admin.status != 1:
        raise AppError("ACCOUNT_DISABLED", "管理员账号已被禁用", status_code=403)
    return admin
