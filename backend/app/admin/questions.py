"""题目管理：增删改查（软删除）、排序、参考图上传、示例提示词、默认次数。

新增题目时在**同一事务内**用 `INSERT IGNORE ... SELECT` 为全体考生补建配额行（需求 4.3 / 风险表）。
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.core.storage import presigned_get_url, upload_bytes
from app.core.timeutil import utcnow
from app.models import Admin, Question, Quota, Work
from app.schemas.common import Message
from app.schemas.question import (
    QuestionAdminOut,
    QuestionCreateIn,
    QuestionSortIn,
    QuestionUpdateIn,
)
from app.services.audit import write_audit
from app.services.moderation import moderate_image
from app.services.quota import backfill_quotas_for_question

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/questions", tags=["管理端 · 题目管理"])

settings = get_settings()

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_CODE_PATTERN = re.compile(r"^Q(\d+)$", re.IGNORECASE)


async def _to_admin_out(question: Question) -> QuestionAdminOut:
    ref_image_url = None
    if question.ref_image_key:
        ref_image_url = await asyncio.to_thread(
            presigned_get_url, settings.MINIO_BUCKET_PUBLIC, question.ref_image_key, 7200
        )
    return QuestionAdminOut(
        id=question.id,
        code=question.code,
        title=question.title,
        description=question.description,
        ref_image_key=question.ref_image_key,
        ref_image_url=ref_image_url,
        prompt_examples=list(question.prompt_examples or []) or None,
        default_quota=question.default_quota,
        sort_order=question.sort_order,
        status=question.status,
        created_at=question.created_at,
        updated_at=question.updated_at,
    )


async def _next_question_code(session: AsyncSession) -> str:
    codes = (await session.execute(select(Question.code).where(Question.code.like("Q%")))).scalars().all()
    max_no = 0
    for code in codes:
        matched = _CODE_PATTERN.match(code or "")
        if matched:
            max_no = max(max_no, int(matched.group(1)))
    return f"Q{max_no + 1}"


async def _ensure_code_unique(session: AsyncSession, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Question.id).where(Question.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Question.id != exclude_id)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise AppError("QUESTION_CODE_EXISTS", f"题号 {code} 已存在", status_code=409)


@router.get("", response_model=list[QuestionAdminOut], summary="题目列表（默认含停用，按 sort_order/id 升序）")
async def list_questions(
    include_disabled: bool = Query(True, description="是否包含已停用（软删除）题目；默认包含"),
    keyword: str | None = Query(None, description="按题号或标题搜索"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[QuestionAdminOut]:
    stmt = select(Question).order_by(Question.sort_order.asc(), Question.id.asc())
    if not include_disabled:
        stmt = stmt.where(Question.status == 1)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Question.code.like(like) | Question.title.like(like))

    questions = (await session.execute(stmt)).scalars().all()
    return [await _to_admin_out(question) for question in questions]


@router.post("", response_model=QuestionAdminOut, summary="新增题目（同事务为全体考生补建配额）")
async def create_question(
    payload: QuestionCreateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuestionAdminOut:
    code = (payload.code or "").strip() or await _next_question_code(session)
    await _ensure_code_unique(session, code)

    question = Question(
        code=code,
        title=payload.title,
        description=payload.description,
        ref_image_key=payload.ref_image_key,
        prompt_examples=payload.prompt_examples,
        default_quota=payload.default_quota,
        sort_order=payload.sort_order,
        status=payload.status,
    )
    session.add(question)
    await session.flush()

    created_quota_rows = await backfill_quotas_for_question(session, question.id, question.default_quota)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="create_question",
        target_type="question",
        target_id=question.id,
        after={
            "code": question.code,
            "title": question.title,
            "default_quota": question.default_quota,
            "status": question.status,
            "quota_rows_created": created_quota_rows,
        },
        remark=f"新增题目 {question.code}，为 {created_quota_rows} 名考生补建配额",
        ip=client_ip(request),
    )
    await session.commit()

    logger.info("新增题目 %s，补建配额行 %s 条", question.code, created_quota_rows)
    await session.refresh(question)
    return await _to_admin_out(question)


@router.post("/sort", response_model=Message, summary="批量排序（按传入顺序）")
async def sort_questions(
    payload: QuestionSortIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Message:
    questions = (
        await session.execute(select(Question).where(Question.id.in_(payload.ids)))
    ).scalars().all()
    if len(questions) != len(set(payload.ids)):
        raise AppError("QUESTION_NOT_FOUND", "部分题目不存在", status_code=404)

    by_id = {question.id: question for question in questions}
    before = {str(question.id): question.sort_order for question in questions}
    for index, question_id in enumerate(payload.ids):
        by_id[question_id].sort_order = index * 10

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="sort_questions",
        target_type="question",
        target_id="batch",
        before=before,
        after={str(question_id): index * 10 for index, question_id in enumerate(payload.ids)},
        remark="调整题目排序",
        ip=client_ip(request),
    )
    await session.commit()
    return Message(message=f"已更新 {len(payload.ids)} 道题的排序")


@router.patch("/{question_id}", response_model=QuestionAdminOut, summary="编辑题目（PATCH）")
@router.put("/{question_id}", response_model=QuestionAdminOut, summary="编辑题目（PUT，等同 PATCH）")
async def update_question(
    question_id: int,
    payload: QuestionUpdateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuestionAdminOut:
    question = await session.get(Question, question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)

    before = {
        "code": question.code,
        "title": question.title,
        "description": question.description,
        "prompt_examples": question.prompt_examples,
        "default_quota": question.default_quota,
        "sort_order": question.sort_order,
        "status": question.status,
    }

    if payload.code is not None and payload.code != question.code:
        await _ensure_code_unique(session, payload.code, exclude_id=question_id)
        question.code = payload.code
    if payload.title is not None:
        question.title = payload.title
    if payload.description is not None:
        question.description = payload.description
    if payload.ref_image_key is not None:
        question.ref_image_key = payload.ref_image_key
    if payload.prompt_examples is not None:
        question.prompt_examples = payload.prompt_examples
    if payload.default_quota is not None:
        question.default_quota = payload.default_quota
    if payload.sort_order is not None:
        question.sort_order = payload.sort_order
    if payload.status is not None:
        question.status = payload.status

    # 停用 → 启用：兜底补齐期间漏建的考生配额行（INSERT IGNORE 幂等）
    reactivated_rows = 0
    if payload.status == 1 and before["status"] != 1:
        reactivated_rows = await backfill_quotas_for_question(session, question.id, question.default_quota)

    after = {
        "code": question.code,
        "title": question.title,
        "description": question.description,
        "prompt_examples": question.prompt_examples,
        "default_quota": question.default_quota,
        "sort_order": question.sort_order,
        "status": question.status,
    }
    if reactivated_rows:
        after["quota_rows_created"] = reactivated_rows

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="update_question",
        target_type="question",
        target_id=question.id,
        before=before,
        after=after,
        remark="编辑题目",
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(question)
    return await _to_admin_out(question)


@router.delete("/{question_id}", response_model=Message, summary="删除题目（有作品记录则 409 拒绝物理删除）")
async def delete_question(
    question_id: int,
    request: Request,
    soft: bool = Query(False, description="true=只停用（软删除），保留题目与配额行"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Message:
    question = await session.get(Question, question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)

    works_count = int(
        (await session.execute(select(func.count()).select_from(Work).where(Work.question_id == question_id))).scalar()
        or 0
    )
    before = {"code": question.code, "title": question.title, "status": question.status}

    # 有作品记录 → 只允许停用，绝不物理删除（否则历史作品与统计对不上账）
    if works_count > 0 and not soft:
        raise AppError(
            "QUESTION_HAS_WORKS",
            f"题目 {question.code} 已有 {works_count} 条作品记录，不能物理删除；请改为停用（status=0）",
            status_code=409,
            detail={"works_count": works_count, "suggestion": "soft_delete"},
        )

    if works_count > 0 or soft:
        if question.status == 0:
            return Message(message=f"题目 {question.code} 已处于停用状态")
        question.status = 0
        await write_audit(
            session,
            admin_id=admin.id,
            admin_username=admin.username,
            action="delete_question",
            target_type="question",
            target_id=question.id,
            before=before,
            after={"code": question.code, "status": 0, "mode": "soft", "works_count": works_count},
            remark="软删除题目（考生端不再展示，历史作品与配额保留）",
            ip=client_ip(request),
        )
        await session.commit()
        return Message(message=f"题目 {question.code} 已停用（软删除）")

    # 无作品 → 物理删除题目及其配额行
    await session.execute(delete(Quota).where(Quota.question_id == question_id))
    await session.delete(question)
    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="delete_question",
        target_type="question",
        target_id=question_id,
        before=before,
        after={"code": before["code"], "deleted": True, "mode": "hard", "works_count": 0},
        remark="物理删除题目（无作品记录）及其配额行",
        ip=client_ip(request),
    )
    await session.commit()
    return Message(message=f"题目 {before['code']} 已删除")


async def _store_ref_image(
    question: Question,
    file: UploadFile,
    request: Request,
    admin: Admin,
    session: AsyncSession,
) -> QuestionAdminOut:
    """校验 → 审核 → 上传 MinIO 公开桶 → 回写 ref_image_key → 审计。"""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise AppError(
            "UNSUPPORTED_IMAGE_TYPE",
            "仅支持 JPG / PNG / WEBP 格式",
            status_code=415,
            detail={"content_type": content_type},
        )

    data = await file.read()
    if not data:
        raise AppError("EMPTY_FILE", "上传文件为空", status_code=400)
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("FILE_TOO_LARGE", "图片不能超过 10MB", status_code=413)

    verdict = await moderate_image(data, file.filename or "ref-image", content_type)
    if not verdict.passed:
        raise AppError("MODERATION_REJECTED", verdict.reason or "图片未通过内容审核", status_code=422)

    ext = ALLOWED_IMAGE_TYPES[content_type]
    key = f"questions/{question.code}/ref{ext}"
    await asyncio.to_thread(upload_bytes, settings.MINIO_BUCKET_PUBLIC, key, data, content_type)

    before_key = question.ref_image_key
    question.ref_image_key = key

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="update_question",
        target_type="question",
        target_id=question.id,
        before={"ref_image_key": before_key},
        after={"ref_image_key": key, "size": len(data)},
        remark="上传题目参考图",
        ip=client_ip(request),
    )
    await session.commit()

    logger.info("题目 %s 参考图已上传：%s (%s bytes)", question.code, key, len(data))
    await session.refresh(question)
    return await _to_admin_out(question)


@router.post("/upload-ref-image", response_model=QuestionAdminOut, summary="上传参考图（form: question_id + file）")
async def upload_ref_image_by_form(
    request: Request,
    question_id: int = Form(..., description="题目 ID"),
    file: UploadFile = File(..., description="JPG / PNG / WEBP，≤10MB"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuestionAdminOut:
    question = await session.get(Question, question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    return await _store_ref_image(question, file, request, admin, session)


@router.post("/{question_id}/ref-image", response_model=QuestionAdminOut, summary="上传参考图（路径参数版）")
async def upload_ref_image(
    question_id: int,
    request: Request,
    file: UploadFile = File(..., description="JPG / PNG / WEBP，≤10MB"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuestionAdminOut:
    question = await session.get(Question, question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)
    return await _store_ref_image(question, file, request, admin, session)
