"""考生管理：Excel 模板 / 导入 / 导出 / 密码条 / 单条 CRUD / 重置密码 / 批量启禁用。

要点（CLAUDE.md 铁律）：
- 导入逐行校验，错误按行号回报且不中断有效行；
- 密码 bcrypt 哈希入库，初始密码另存 Fernet 密文，过期不可查看；
- 导入考生、重置密码、启禁用全部写 audit_logs（含 before/after）；
- 明文密码只出现在「重置/新增响应、导出、密码条」三处，不进日志。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.core.security import (
    decrypt_init_pwd,
    generate_exam_password,
    init_pwd_expire_at,
)
from app.core.sessions import destroy_user_sessions
from app.core.timeutil import to_iso_utc, utcnow
from app.models import Admin, Candidate, Question, Quota
from app.schemas.candidate import (
    BatchIdsIn,
    BatchResetOut,
    BatchStatusIn,
    CandidateCreateIn,
    CandidateOut,
    CandidatePageOut,
    CandidateUpdateIn,
    ImportResultOut,
    ImportRowErrorOut,
    PasswordSlipOut,
    ResetPasswordOut,
)
from app.schemas.common import Message
from app.services.audit import write_audit
from app.services.excel import (
    EXPORT_HEADERS,
    ParsedCandidateRow,
    build_csv_bytes,
    build_export_bytes,
    build_password_slips_bytes,
    build_template_bytes,
    parse_candidate_rows,
)
from app.services.hashing import hash_and_encrypt_many
from app.services.quota import backfill_quotas_for_candidate, backfill_quotas_for_candidates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/candidates", tags=["管理端 · 考生管理"])

settings = get_settings()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXPIRED_PASSWORD_TEXT = "已过期，请重置"
_IMPORT_CHUNK = 500


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _chunks(items: Sequence[object], size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


async def _hash_and_encrypt(passwords: list[str]) -> list[tuple[str, str]]:
    """并行计算 (bcrypt 哈希, Fernet 密文)：ProcessPoolExecutor 为主，线程池兜底。

    cost 取 `BCRYPT_ROUNDS`（默认 10），绝不为了速度降级算法。
    """
    if not passwords:
        return []
    return await hash_and_encrypt_many(
        passwords,
        rounds=int(settings.BCRYPT_ROUNDS),
        fernet_key=settings.AES_FERNET_KEY,
    )


def _pwd_viewable(candidate: Candidate, now: datetime) -> bool:
    return bool(candidate.init_password_enc) and candidate.init_pwd_expire_at is not None and candidate.init_pwd_expire_at > now


def _plain_init_password(candidate: Candidate, now: datetime) -> str | None:
    """可查看期内返回明文初始密码，否则 None。"""
    if not _pwd_viewable(candidate, now):
        return None
    return decrypt_init_pwd(candidate.init_password_enc or "")


def _candidate_out(candidate: Candidate, total: int, used: int, now: datetime) -> CandidateOut:
    return CandidateOut(
        id=candidate.id,
        account=candidate.account,
        name=candidate.name,
        status=candidate.status,
        remark=candidate.remark,
        last_login_at=candidate.last_login_at,
        last_login_ip=candidate.last_login_ip,
        created_at=candidate.created_at,
        quota_total_sum=int(total or 0),
        quota_used_sum=int(used or 0),
        init_pwd_viewable=_pwd_viewable(candidate, now),
    )


async def _existing_accounts(session: AsyncSession, accounts: Sequence[str]) -> set[str]:
    """批量查库内已存在的账号（分批 IN，避免超长 SQL）。"""
    found: set[str] = set()
    unique = list({account for account in accounts if account})
    for chunk in _chunks(unique, 500):
        rows = await session.execute(select(Candidate.account).where(Candidate.account.in_(chunk)))
        found.update(row[0] for row in rows)
    return found


def _format_account(seq: int) -> str:
    """自动考号格式：`AUTO_ACCOUNT_PREFIX` + 指定位数递增序号（如 C2026000001）。"""
    return f"{settings.AUTO_ACCOUNT_PREFIX}{seq:0{int(settings.AUTO_ACCOUNT_DIGITS)}d}"


async def _next_auto_seq(session: AsyncSession, taken: set[str]) -> int:
    """自动账号序号：扫描 `<prefix><数字>` 形式的历史账号，取最大值 +1。"""
    prefix = settings.AUTO_ACCOUNT_PREFIX
    rows = await session.execute(select(Candidate.account).where(Candidate.account.like(f"{prefix}%")))
    max_seq = 0
    for (account,) in rows:
        suffix = account[len(prefix) :]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    seq = max_seq + 1
    while _format_account(seq) in taken:
        seq += 1
    return seq


def _build_new_candidate(
    *,
    name: str,
    account: str,
    remark: str | None,
    password_hash: str,
    init_password_enc: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "account": account,
        "name": name,
        "password_hash": password_hash,
        "init_password_enc": init_password_enc,
        "init_pwd_expire_at": init_pwd_expire_at(now),
        "status": 1,
        "remark": remark,
        "failed_login_count": 0,
    }


def _ensure_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise AppError("NAME_REQUIRED", "姓名不能为空", status_code=400)
    if len(cleaned) > 64:
        raise AppError("NAME_TOO_LONG", "姓名长度不能超过 64 个字符", status_code=400)
    return cleaned


def _ensure_account(account: str) -> str:
    cleaned = (account or "").strip()
    if len(cleaned) > 64:
        raise AppError("ACCOUNT_TOO_LONG", "账号长度不能超过 64 个字符", status_code=400)
    return cleaned


# --------------------------------------------------------------------------- #
# 模板 / 导入 / 导出 / 密码条
# --------------------------------------------------------------------------- #
@router.get("/template", summary="下载 Excel 导入模板")
async def download_template(admin: Admin = Depends(get_current_admin)) -> StreamingResponse:
    content = build_template_bytes()
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="candidate_import_template.xlsx"'},
    )


@router.post("/import", response_model=ImportResultOut, summary="Excel 批量导入考生")
async def import_candidates(
    request: Request,
    file: UploadFile = File(..., description="按模板填写的 .xlsx 文件"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ImportResultOut:
    started = time.perf_counter()
    data = await file.read()

    if not data:
        raise AppError("EMPTY_FILE", "上传文件为空", status_code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE", "文件超过 20MB，请拆分后导入", status_code=413)
    if not data.startswith(b"PK"):
        raise AppError("INVALID_EXCEL_FILE", "请上传 .xlsx 文件（模板可在本接口的 template 下载）", status_code=415)

    try:
        # openpyxl 是同步 CPU 活，放线程里跑，别堵事件循环
        parsed = await asyncio.to_thread(parse_candidate_rows, data)
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都归为文件格式错误
        logger.warning("Excel 解析失败：%s", exc)
        raise AppError("INVALID_EXCEL_FILE", "无法解析该 Excel 文件，请使用标准模板", status_code=415) from exc

    if not parsed:
        raise AppError("EMPTY_EXCEL", "文件中没有可导入的数据行", status_code=400)
    if len(parsed) > settings.EXCEL_IMPORT_MAX_ROWS:
        raise AppError(
            "TOO_MANY_ROWS",
            f"单次最多导入 {settings.EXCEL_IMPORT_MAX_ROWS} 行，请拆分后导入",
            status_code=400,
        )

    errors: list[ImportRowErrorOut] = []

    # 1) 行内校验 + 文件内查重
    seen: dict[str, int] = {}
    candidates_after_file_check: list[ParsedCandidateRow] = []
    for row in parsed:
        name = (row.name or "").strip()
        if not name:
            errors.append(ImportRowErrorOut(row=row.row_no, name=None, account=row.account, reason="姓名不能为空"))
            continue
        if len(name) > 64:
            errors.append(ImportRowErrorOut(row=row.row_no, name=name[:64], account=row.account, reason="姓名超过 64 个字符"))
            continue
        if row.account:
            account = row.account.strip()
            if len(account) > 64:
                errors.append(ImportRowErrorOut(row=row.row_no, name=name, account=account[:64], reason="账号超过 64 个字符"))
                continue
            if account in seen:
                errors.append(
                    ImportRowErrorOut(
                        row=row.row_no,
                        name=name,
                        account=account,
                        reason=f"账号在文件中重复（与第 {seen[account]} 行相同）",
                    )
                )
                continue
            seen[account] = row.row_no
            row.account = account
        row.name = name
        candidates_after_file_check.append(row)

    # 2) 库内查重
    existing = await _existing_accounts(session, [row.account for row in candidates_after_file_check if row.account])
    survivors: list[ParsedCandidateRow] = []
    for row in candidates_after_file_check:
        if row.account and row.account in existing:
            errors.append(
                ImportRowErrorOut(row=row.row_no, name=row.name, account=row.account, reason="账号已存在")
            )
            continue
        survivors.append(row)

    # 3) 账号留空 → 按规则自动生成
    taken = set(existing) | set(seen)
    next_seq = await _next_auto_seq(session, taken)
    for row in survivors:
        if row.account:
            continue
        while True:
            generated = _format_account(next_seq)
            next_seq += 1
            if generated not in taken:
                break
        row.account = generated
        taken.add(generated)

    # 4) 生成初始密码，并用进程池并行算 bcrypt 哈希 + Fernet 密文
    t_hash = time.perf_counter()
    plain_passwords = [generate_exam_password() for _ in survivors]
    hashed_pairs = await _hash_and_encrypt(plain_passwords)
    hash_ms = int((time.perf_counter() - t_hash) * 1000)

    # 5) 批量落库（分批 multi-row insert）
    t_db = time.perf_counter()
    now = utcnow()
    payload = [
        _build_new_candidate(
            name=row.name,
            account=row.account or "",
            remark=row.remark,
            password_hash=hashed_pairs[index][0],
            init_password_enc=hashed_pairs[index][1],
            now=now,
        )
        for index, row in enumerate(survivors)
    ]
    for chunk in _chunks(payload, _IMPORT_CHUNK):
        await session.execute(insert(Candidate), chunk)

    # 6) 取回自增 ID 并为每个新考生补齐所有启用题目的配额行
    new_ids: list[int] = []
    if payload:
        accounts = [str(item["account"]) for item in payload]
        for chunk in _chunks(accounts, 500):
            rows = await session.execute(select(Candidate.id).where(Candidate.account.in_(chunk)))
            new_ids.extend(row[0] for row in rows)
        await backfill_quotas_for_candidates(session, new_ids)

    # 7) 审计
    batch_no = uuid.uuid4().hex[:12]
    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="import_users",
        target_type="candidate",
        target_id=batch_no,
        after={
            "batch_no": batch_no,
            "success_count": len(payload),
            "failed_count": len(errors),
            "total_rows": len(parsed),
            "accounts": [str(item["account"]) for item in payload][:100],
        },
        remark=f"Excel 导入考生：成功 {len(payload)} 行，失败 {len(errors)} 行",
        ip=client_ip(request),
    )
    await session.commit()

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    db_ms = int((time.perf_counter() - t_db) * 1000)
    logger.info(
        "Excel 导入完成：成功 %s / 失败 %s，耗时 %sms（哈希 %sms / 落库+配额 %sms）",
        len(payload),
        len(errors),
        elapsed_ms,
        hash_ms,
        db_ms,
    )

    return ImportResultOut(
        batch_no=batch_no,
        total_rows=len(parsed),
        success_count=len(payload),
        failed_count=len(errors),
        errors=errors,
        elapsed_ms=elapsed_ms,
    )


@router.get("/export", summary="导出考生账号与初始密码（xlsx / csv，可按勾选导出）")
async def export_candidates(
    fmt: str = Query("xlsx", alias="format", pattern="^(xlsx|csv)$"),
    ids: str | None = Query(None, description="逗号分隔的考生 ID；不传则导出全部"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    now = utcnow()
    stmt = select(Candidate).order_by(Candidate.account.asc())
    if ids:
        try:
            id_list = [int(item) for item in ids.split(",") if item.strip()]
        except ValueError as exc:
            raise AppError("INVALID_IDS", "ids 必须是逗号分隔的数字", status_code=400) from exc
        if not id_list:
            raise AppError("INVALID_IDS", "ids 不能为空", status_code=400)
        stmt = stmt.where(Candidate.id.in_(id_list))

    candidates = (await session.execute(stmt)).scalars().all()

    rows = [
        (
            candidate.account,
            candidate.name,
            _plain_init_password(candidate, now) or EXPIRED_PASSWORD_TEXT,
            "启用" if candidate.status == 1 else "禁用",
            candidate.remark or "",
        )
        for candidate in candidates
    ]

    stamp = now.strftime("%Y%m%d_%H%M%S")
    if fmt == "csv":
        content = build_csv_bytes(EXPORT_HEADERS, rows)
        media_type = "text/csv; charset=utf-8"
        filename = f"candidates_{stamp}.csv"
    else:
        content = build_export_bytes(rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"candidates_{stamp}.xlsx"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/password-slips", summary="密码条数据（供前端打印，或直接下载 xlsx）")
async def password_slips(
    ids: str | None = Query(None, description="逗号分隔的考生 ID；不传则导出全部"),
    fmt: str = Query("json", alias="format", pattern="^(json|xlsx)$"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    now = utcnow()
    stmt = select(Candidate).order_by(Candidate.account.asc())
    if ids:
        try:
            id_list = [int(item) for item in ids.split(",") if item.strip()]
        except ValueError as exc:
            raise AppError("INVALID_IDS", "ids 必须是逗号分隔的数字", status_code=400) from exc
        if not id_list:
            raise AppError("INVALID_IDS", "ids 不能为空", status_code=400)
        stmt = stmt.where(Candidate.id.in_(id_list))

    candidates = (await session.execute(stmt)).scalars().all()
    items: list[PasswordSlipOut] = []
    for candidate in candidates:
        password = _plain_init_password(candidate, now)
        items.append(
            PasswordSlipOut(
                id=candidate.id,
                account=candidate.account,
                name=candidate.name,
                password=password,
                expire_at=candidate.init_pwd_expire_at,
                expired=password is None,
            )
        )

    if fmt == "xlsx":
        content = build_password_slips_bytes(
            [
                {
                    "account": item.account,
                    "name": item.name,
                    "password": item.password,
                    "expire_at": to_iso_utc(item.expire_at),
                }
                for item in items
            ]
        )
        stamp = now.strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(
            iter([content]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="password_slips_{stamp}.xlsx"'},
        )

    return items


@router.get("/{candidate_id}/init-password", response_model=PasswordSlipOut, summary="查看单个考生的初始密码")
async def view_init_password(
    candidate_id: int,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> PasswordSlipOut:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "考生不存在", status_code=404)

    now = utcnow()
    password = _plain_init_password(candidate, now)
    if password is None:
        # 手册要求：已过期或从未生成 → 410，前端提示「已过期，请重置」
        raise AppError(
            "INIT_PASSWORD_EXPIRED",
            "初始密码已过期或尚未生成，请重置密码后再查看",
            status_code=410,
            detail={"account": candidate.account, "expire_at": to_iso_utc(candidate.init_pwd_expire_at)},
        )
    return PasswordSlipOut(
        id=candidate.id,
        account=candidate.account,
        name=candidate.name,
        password=password,
        expire_at=candidate.init_pwd_expire_at,
        expired=False,
    )


# --------------------------------------------------------------------------- #
# 列表 / 新增 / 编辑 / 删除
# --------------------------------------------------------------------------- #
@router.get("", response_model=CandidatePageOut, summary="考生列表（分页、按账号/姓名搜索）")
async def list_candidates(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    keyword: str | None = Query(None, description="按账号或姓名模糊搜索"),
    status: int | None = Query(None, ge=0, le=1),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> CandidatePageOut:
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(Candidate.account.like(like), Candidate.name.like(like)))
    if status is not None:
        conditions.append(Candidate.status == status)

    total = int(
        (await session.execute(select(func.count()).select_from(Candidate).where(*conditions))).scalar() or 0
    )

    quota_sub = (
        select(
            Quota.candidate_id.label("candidate_id"),
            func.coalesce(func.sum(Quota.quota_total), 0).label("quota_total_sum"),
            func.coalesce(func.sum(Quota.quota_used), 0).label("quota_used_sum"),
        )
        .join(Question, Question.id == Quota.question_id)
        .where(Question.status == 1)
        .group_by(Quota.candidate_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(Candidate, quota_sub.c.quota_total_sum, quota_sub.c.quota_used_sum)
            .outerjoin(quota_sub, quota_sub.c.candidate_id == Candidate.id)
            .where(*conditions)
            .order_by(Candidate.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    now = utcnow()
    items = [
        _candidate_out(candidate, int(total_sum or 0), int(used_sum or 0), now)
        for candidate, total_sum, used_sum in rows
    ]
    return CandidatePageOut(total=total, page=page, size=size, items=items)


@router.post("", response_model=ResetPasswordOut, summary="新增单个考生")
async def create_candidate(
    payload: CandidateCreateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ResetPasswordOut:
    name = _ensure_name(payload.name)
    account = _ensure_account(payload.account or "")

    if account:
        exists = (await session.execute(select(Candidate.id).where(Candidate.account == account))).scalar_one_or_none()
        if exists is not None:
            raise AppError("ACCOUNT_EXISTS", f"账号 {account} 已存在", status_code=409)
    else:
        account = _format_account(await _next_auto_seq(session, set()))

    password = generate_exam_password()
    password_hash, init_password_enc = (await _hash_and_encrypt([password]))[0]
    now = utcnow()
    candidate = Candidate(
        **_build_new_candidate(
            name=name,
            account=account,
            remark=payload.remark,
            password_hash=password_hash,
            init_password_enc=init_password_enc,
            now=now,
        )
    )
    session.add(candidate)
    await session.flush()

    quota_rows = await backfill_quotas_for_candidate(session, candidate.id)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="create_candidate",
        target_type="candidate",
        target_id=candidate.id,
        after={"account": candidate.account, "name": candidate.name, "remark": candidate.remark, "quota_rows": quota_rows},
        remark="新增考生",
        ip=client_ip(request),
    )
    await session.commit()

    return ResetPasswordOut(id=candidate.id, account=candidate.account, name=candidate.name, password=password)


@router.patch("/{candidate_id}", response_model=CandidateOut, summary="编辑单个考生（PATCH）")
@router.put("/{candidate_id}", response_model=CandidateOut, summary="编辑单个考生（PUT，等同 PATCH）")
async def update_candidate(
    candidate_id: int,
    payload: CandidateUpdateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> CandidateOut:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "考生不存在", status_code=404)

    before = {
        "account": candidate.account,
        "name": candidate.name,
        "remark": candidate.remark,
        "status": candidate.status,
    }

    if payload.account is not None and payload.account != candidate.account:
        duplicated = (
            await session.execute(
                select(Candidate.id).where(Candidate.account == payload.account, Candidate.id != candidate_id)
            )
        ).scalar_one_or_none()
        if duplicated is not None:
            raise AppError("ACCOUNT_EXISTS", f"账号 {payload.account} 已被占用", status_code=409)
        candidate.account = payload.account

    if payload.name is not None:
        candidate.name = _ensure_name(payload.name)
    if payload.remark is not None:
        candidate.remark = payload.remark
    if payload.status is not None:
        candidate.status = payload.status

    after = {
        "account": candidate.account,
        "name": candidate.name,
        "remark": candidate.remark,
        "status": candidate.status,
    }
    action = "update_candidate"
    if before["status"] != after["status"]:
        action = "toggle_status"

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action=action,
        target_type="candidate",
        target_id=candidate.id,
        before=before,
        after=after,
        remark="编辑考生信息" if action == "update_candidate" else ("启用考生" if candidate.status == 1 else "禁用考生"),
        ip=client_ip(request),
    )
    await session.commit()

    await session.refresh(candidate)
    total, used = await _candidate_quota_sums(session, candidate.id)
    return _candidate_out(candidate, total, used, utcnow())


async def _candidate_quota_sums(session: AsyncSession, candidate_id: int) -> tuple[int, int]:
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Quota.quota_total), 0),
                func.coalesce(func.sum(Quota.quota_used), 0),
            )
            .join(Question, Question.id == Quota.question_id)
            .where(Quota.candidate_id == candidate_id, Question.status == 1)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


@router.delete("/{candidate_id}", response_model=Message, summary="删除单个考生")
async def delete_candidate(
    candidate_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Message:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "考生不存在", status_code=404)

    before = {"account": candidate.account, "name": candidate.name, "status": candidate.status}
    await session.execute(delete(Quota).where(Quota.candidate_id == candidate_id))
    await session.delete(candidate)
    await destroy_user_sessions("candidate", [candidate_id])

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="delete_candidate",
        target_type="candidate",
        target_id=candidate_id,
        before=before,
        remark="删除考生（历史作品保留）",
        ip=client_ip(request),
    )
    await session.commit()
    return Message(message=f"考生 {before['account']} 已删除")


# --------------------------------------------------------------------------- #
# 重置密码 / 批量操作
# --------------------------------------------------------------------------- #
@router.post("/{candidate_id}/reset-password", response_model=ResetPasswordOut, summary="重置单个考生密码")
async def reset_password(
    candidate_id: int,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ResetPasswordOut:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "考生不存在", status_code=404)

    password = generate_exam_password()
    candidate.password_hash, candidate.init_password_enc = (await _hash_and_encrypt([password]))[0]
    candidate.init_pwd_expire_at = init_pwd_expire_at()
    candidate.failed_login_count = 0
    candidate.locked_until = None
    await destroy_user_sessions("candidate", [candidate.id])

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="reset_pwd",
        target_type="candidate",
        target_id=candidate.id,
        before={"account": candidate.account, "name": candidate.name},
        after={"account": candidate.account, "password_reset": True},
        remark="重置考生密码",
        ip=client_ip(request),
    )
    await session.commit()

    return ResetPasswordOut(id=candidate.id, account=candidate.account, name=candidate.name, password=password)


@router.post("/batch-reset-password", response_model=BatchResetOut, summary="批量重置密码（并立即踢下线）")
async def batch_reset_password(
    payload: BatchIdsIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BatchResetOut:
    id_list = payload.id_list
    candidates = (
        await session.execute(select(Candidate).where(Candidate.id.in_(id_list)).order_by(Candidate.id.asc()))
    ).scalars().all()
    if not candidates:
        raise AppError("CANDIDATE_NOT_FOUND", "没有找到任何符合条件的考生", status_code=404)

    passwords = [generate_exam_password() for _ in candidates]
    hashed_pairs = await _hash_and_encrypt(passwords)
    expire = init_pwd_expire_at()

    items: list[ResetPasswordOut] = []
    for candidate, password, (password_hash, init_enc) in zip(candidates, passwords, hashed_pairs):
        candidate.password_hash = password_hash
        candidate.init_password_enc = init_enc
        candidate.init_pwd_expire_at = expire
        candidate.failed_login_count = 0
        candidate.locked_until = None
        items.append(
            ResetPasswordOut(id=candidate.id, account=candidate.account, name=candidate.name, password=password)
        )

    # 重置密码必须让旧登录态立即失效
    kicked = await destroy_user_sessions("candidate", [candidate.id for candidate in candidates])

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="reset_pwd",
        target_type="candidate",
        target_id="batch" if len(items) > 1 else str(items[0].id),
        after={"count": len(items), "sessions_killed": kicked, "accounts": [item.account for item in items][:100]},
        remark=f"批量重置密码：{len(items)} 人，踢下线 {kicked} 个会话",
        ip=client_ip(request),
    )
    await session.commit()
    return BatchResetOut(items=items)


@router.post("/batch-status", response_model=Message, summary="批量启用 / 禁用（禁用立即踢下线）")
async def batch_status(
    payload: BatchStatusIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Message:
    id_list = payload.id_list
    candidates = (
        await session.execute(select(Candidate).where(Candidate.id.in_(id_list)))
    ).scalars().all()
    if not candidates:
        raise AppError("CANDIDATE_NOT_FOUND", "没有找到任何符合条件的考生", status_code=404)

    before = {str(candidate.id): candidate.status for candidate in candidates}
    await session.execute(
        update(Candidate).where(Candidate.id.in_(id_list)).values(status=payload.status)
    )

    # 禁用后其已登录会话必须立刻失效（不影响已在跑的生成任务）
    kicked = 0
    if payload.status == 0:
        kicked = await destroy_user_sessions("candidate", id_list)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="toggle_status",
        target_type="candidate",
        target_id="batch",
        before=before,
        after={"status": payload.status, "count": len(candidates), "sessions_killed": kicked},
        remark=f"批量{'启用' if payload.status == 1 else '禁用'}考生：{len(candidates)} 人",
        ip=client_ip(request),
    )
    await session.commit()

    verb = "启用" if payload.status == 1 else "禁用"
    suffix = f"，已强制下线 {kicked} 个会话" if kicked else ""
    return Message(message=f"已{verb} {len(candidates)} 名考生{suffix}")
