"""考生端题目列表：返回启用题目 + 本人每题余量与「已调整」标记（需求 4.3）。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import get_current_candidate
from app.core.storage import presigned_get_url
from app.models import Candidate, Question, Quota
from app.schemas.question import QuestionPublicOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["考生端 · 题目"])

settings = get_settings()


@router.get("/questions", response_model=list[QuestionPublicOut], summary="题目列表（含剩余次数）")
async def list_questions(
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> list[QuestionPublicOut]:
    rows = (
        await session.execute(
            select(Question, Quota)
            .outerjoin(
                Quota,
                and_(Quota.question_id == Question.id, Quota.candidate_id == candidate.id),
            )
            .where(Question.status == 1)
            .order_by(Question.sort_order.asc(), Question.id.asc())
        )
    ).all()

    items: list[QuestionPublicOut] = []
    for question, quota in rows:
        quota_total = quota.quota_total if quota else question.default_quota
        quota_used = quota.quota_used if quota else 0
        ref_image_url = None
        if question.ref_image_key:
            ref_image_url = await asyncio.to_thread(
                presigned_get_url, settings.MINIO_BUCKET_PUBLIC, question.ref_image_key, 7200
            )
        items.append(
            QuestionPublicOut(
                id=question.id,
                code=question.code,
                title=question.title,
                description=question.description,
                ref_image_url=ref_image_url,
                prompt_examples=list(question.prompt_examples or []) or None,
                default_quota=question.default_quota,
                quota_total=quota_total,
                quota_used=quota_used,
                remaining=max(quota_total - quota_used, 0),
                adjusted_flag=bool(quota.adjusted_flag) if quota else False,
            )
        )
    return items
