"""考生端认证：登录 / 退出 / 我的信息。

会话隔离（铁律 6）：考生只认 `cand_sid`，管理员只认 `admin_sid`，两条链路互不通用。
登录保护（需求 4.1）：连续输错 LOGIN_MAX_FAILURES 次锁定 LOGIN_LOCK_MINUTES 分钟。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import client_ip, get_current_candidate
from app.core.errors import AppError
from app.core.security import verify_password
from app.core.sessions import CANDIDATE_COOKIE, clear_session_cookie, create_session, destroy_session, set_session_cookie
from app.core.timeutil import utcnow
from app.models import Candidate
from app.schemas.auth import CandidateLoginIn, CandidateMeOut, LogoutOut
from app.services.quota import candidate_quota_totals
from app.services.time_window import current_window_snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["考生端 · 认证"])

settings = get_settings()


def _me_payload(
    candidate: Candidate,
    total: int,
    used: int,
    window: dict[str, object] | None = None,
) -> CandidateMeOut:
    snapshot = window or {}
    current = snapshot.get("current_window")
    return CandidateMeOut(
        id=candidate.id,
        account=candidate.account,
        name=candidate.name,
        status=candidate.status,
        last_login_at=candidate.last_login_at,
        total_quota=total,
        total_used=used,
        total_remaining=max(total - used, 0),
        in_window=bool(snapshot.get("in_window", False)),
        current_window_remaining_sec=snapshot.get("current_window_remaining_sec"),
        next_window_start=snapshot.get("next_window_start"),
        current_window_end=(current or {}).get("end_at") if isinstance(current, dict) else None,
    )


@router.post("/login", response_model=CandidateMeOut, summary="考生登录")
async def candidate_login(
    payload: CandidateLoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> CandidateMeOut:
    account = payload.account.strip()
    candidate = (
        await session.execute(select(Candidate).where(Candidate.account == account))
    ).scalar_one_or_none()

    if candidate is None:
        # 与密码错误同一提示，避免账号枚举
        raise AppError("INVALID_CREDENTIALS", "账号或密码错误", status_code=401)
    if candidate.status != 1:
        raise AppError("ACCOUNT_DISABLED", "账号已被禁用，请联系管理员", status_code=403)

    now = utcnow()
    if candidate.locked_until and candidate.locked_until > now:
        remain = max(1, int((candidate.locked_until - now).total_seconds()) + 1)
        raise AppError(
            "ACCOUNT_LOCKED",
            f"连续输错次数过多，账号已锁定，请 {remain} 秒后再试",
            status_code=429,
            detail={"retry_after": remain},
        )

    if not await asyncio.to_thread(verify_password, payload.password, candidate.password_hash):
        candidate.failed_login_count += 1
        locked = candidate.failed_login_count >= settings.LOGIN_MAX_FAILURES
        if locked:
            candidate.locked_until = now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
        await session.commit()

        if locked:
            remain = settings.LOGIN_LOCK_MINUTES * 60
            logger.warning("考生 %s 连续登录失败 %s 次，锁定至 %s", candidate.account, candidate.failed_login_count, candidate.locked_until)
            raise AppError(
                "ACCOUNT_LOCKED",
                f"连续输错 {settings.LOGIN_MAX_FAILURES} 次，账号已锁定 {settings.LOGIN_LOCK_MINUTES} 分钟",
                status_code=429,
                detail={"retry_after": remain},
            )

        left = settings.LOGIN_MAX_FAILURES - candidate.failed_login_count
        raise AppError(
            "INVALID_CREDENTIALS",
            f"账号或密码错误，还可尝试 {left} 次",
            status_code=401,
            detail={"remaining_attempts": left},
        )

    candidate.failed_login_count = 0
    candidate.locked_until = None
    candidate.last_login_at = now
    candidate.last_login_ip = client_ip(request)
    await session.commit()

    sid = await create_session("candidate", candidate.id)
    set_session_cookie(response, "candidate", sid)
    logger.info("考生 %s 登录成功，IP=%s", candidate.account, candidate.last_login_ip)

    total, used = await candidate_quota_totals(session, candidate.id)
    window = await current_window_snapshot(session)
    return _me_payload(candidate, total, used, window)


@router.post("/logout", response_model=LogoutOut, summary="考生退出登录")
async def candidate_logout(request: Request, response: Response) -> LogoutOut:
    await destroy_session("candidate", request.cookies.get(CANDIDATE_COOKIE))
    clear_session_cookie(response, "candidate")
    return LogoutOut()


@router.get("/me", response_model=CandidateMeOut, summary="我的信息、总配额与开放时段快照")
async def candidate_me(
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> CandidateMeOut:
    total, used = await candidate_quota_totals(session, candidate.id)
    window = await current_window_snapshot(session)
    return _me_payload(candidate, total, used, window)
