"""管理端配额精细调控：按题批量、按人设置、多人加减，全部带下调保护与审计快照。

下调保护（验收标准 #5）：只要存在 `quota_used > 目标 quota_total` 的考生且未 `force_confirm`，
一律返回 409 并附带受影响人数与明细，绝不静默把「已用次数」压到「总次数」之上。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, get_current_admin
from app.core.errors import AppError
from app.models import Admin, Candidate, Question, Quota
from app.schemas.quota import (
    BatchAdjustIn,
    BatchByQuestionIn,
    ByCandidateIn,
    QuotaChangeOut,
    QuotaPageOut,
    QuotaRowOut,
)
from app.services.audit import write_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/quotas", tags=["管理端 · 配额调控"])

_MAX_SAMPLES = 20


def _row_out(quota: Quota, candidate: Candidate, question: Question) -> QuotaRowOut:
    return QuotaRowOut(
        id=quota.id,
        candidate_id=quota.candidate_id,
        account=candidate.account,
        name=candidate.name,
        question_id=quota.question_id,
        question_code=question.code,
        question_title=question.title,
        quota_total=quota.quota_total,
        quota_used=quota.quota_used,
        quota_remaining=max(quota.quota_total - quota.quota_used, 0),
        adjusted_flag=bool(quota.adjusted_flag),
        updated_at=quota.updated_at,
    )


@router.get("", response_model=QuotaPageOut, summary="配额明细（按考生/关键字/题目筛选，分页）")
async def list_quotas(
    candidate_id: int | None = Query(None, gt=0),
    keyword: str | None = Query(None, description="按账号或姓名模糊匹配"),
    question_id: int | None = Query(None, gt=0),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuotaPageOut:
    conditions = []
    if candidate_id is not None:
        conditions.append(Quota.candidate_id == candidate_id)
    if question_id is not None:
        conditions.append(Quota.question_id == question_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append((Candidate.account.like(like)) | (Candidate.name.like(like)))

    base = (
        select(Quota, Candidate, Question)
        .join(Candidate, Candidate.id == Quota.candidate_id)
        .join(Question, Question.id == Quota.question_id)
        .where(*conditions)
    )

    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Quota)
                .join(Candidate, Candidate.id == Quota.candidate_id)
                .join(Question, Question.id == Quota.question_id)
                .where(*conditions)
            )
        ).scalar()
        or 0
    )

    rows = (
        await session.execute(
            base.order_by(Quota.candidate_id.asc(), Quota.question_id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = [_row_out(quota, candidate, question) for quota, candidate, question in rows]
    return QuotaPageOut(total=total, page=page, size=size, items=items)


@router.post("/batch-by-question", response_model=QuotaChangeOut, summary="按题批量设置次数（下调需二次确认）")
async def batch_by_question(
    payload: BatchByQuestionIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuotaChangeOut:
    question = await session.get(Question, payload.question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)

    blocked_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Quota)
                .where(Quota.question_id == payload.question_id, Quota.quota_used > payload.quota_total)
            )
        ).scalar()
        or 0
    )

    if blocked_count and not payload.force_confirm:
        samples = (
            await session.execute(
                select(Candidate.account, Candidate.name, Quota.quota_used)
                .join(Quota, Quota.candidate_id == Candidate.id)
                .where(Quota.question_id == payload.question_id, Quota.quota_used > payload.quota_total)
                .order_by(Quota.quota_used.desc())
                .limit(_MAX_SAMPLES)
            )
        ).all()
        raise AppError(
            "QUOTA_DOWNGRADE_CONFIRM_REQUIRED",
            f"题目 {question.code} 存在 {blocked_count} 名考生的已用次数大于目标值 {payload.quota_total}，请二次确认",
            status_code=409,
            detail={
                "affected_count": blocked_count,
                "target_quota_total": payload.quota_total,
                "samples": [
                    {"account": account, "name": name, "quota_used": int(used)}
                    for account, name, used in samples
                ],
            },
        )

    distribution = (
        await session.execute(
            select(Quota.quota_total, func.count())
            .where(Quota.question_id == payload.question_id)
            .group_by(Quota.quota_total)
        )
    ).all()
    before = {
        "question_id": payload.question_id,
        "question_code": question.code,
        "quota_total_distribution": {int(total): int(count) for total, count in distribution},
    }

    result = await session.execute(
        update(Quota)
        .where(Quota.question_id == payload.question_id)
        .values(quota_total=payload.quota_total, adjusted_flag=1)
    )
    updated = int(result.rowcount or 0)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="adjust_quota",
        target_type="quota",
        target_id=f"question:{payload.question_id}",
        before=before,
        after={
            "question_id": payload.question_id,
            "question_code": question.code,
            "quota_total": payload.quota_total,
            "updated_rows": updated,
            "force_confirm": payload.force_confirm,
            "blocked_rows": blocked_count,
        },
        remark=payload.remark or f"按题批量设置 {question.code} 次数为 {payload.quota_total}",
        ip=client_ip(request),
    )
    await session.commit()

    logger.info("按题批量调整：question=%s -> %s，更新 %s 行", question.code, payload.quota_total, updated)
    return QuotaChangeOut(updated=updated, blocked=blocked_count)


@router.put("/by-candidate/{candidate_id}", response_model=QuotaChangeOut, summary="按人设置次数（逐题 / 一键统一）")
async def by_candidate(
    candidate_id: int,
    payload: ByCandidateIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuotaChangeOut:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "考生不存在", status_code=404)

    # 目标值：逐题 或 一键统一（只针对启用题目）
    if payload.uniform_quota is not None:
        enabled = (
            await session.execute(select(Question).where(Question.status == 1).order_by(Question.id.asc()))
        ).scalars().all()
        targets = [(question, int(payload.uniform_quota)) for question in enabled]
    else:
        targets = []
        for item in payload.items or []:
            question = await session.get(Question, item.question_id)
            if question is None:
                raise AppError("QUESTION_NOT_FOUND", f"题目 {item.question_id} 不存在", status_code=404)
            targets.append((question, int(item.quota_total)))

    if not targets:
        raise AppError("EMPTY_TARGETS", "没有可调整的题目", status_code=400)

    existing = {
        quota.question_id: quota
        for quota in (
            await session.execute(
                select(Quota).where(
                    Quota.candidate_id == candidate_id,
                    Quota.question_id.in_([question.id for question, _ in targets]),
                )
            )
        ).scalars().all()
    }

    blocked: list[dict[str, object]] = []
    for question, target in targets:
        quota = existing.get(question.id)
        used = quota.quota_used if quota else 0
        if used > target:
            blocked.append(
                {"question_id": question.id, "question_code": question.code, "quota_used": used, "target": target}
            )

    if blocked and not payload.force_confirm:
        raise AppError(
            "QUOTA_DOWNGRADE_CONFIRM_REQUIRED",
            f"考生 {candidate.account} 有 {len(blocked)} 道题的已用次数大于目标值，请二次确认",
            status_code=409,
            detail={"affected_count": len(blocked), "samples": blocked},
        )

    before = {
        "candidate_id": candidate_id,
        "account": candidate.account,
        "items": [
            {
                "question_id": question.id,
                "question_code": question.code,
                "quota_total": existing[question.id].quota_total if question.id in existing else None,
                "quota_used": existing[question.id].quota_used if question.id in existing else 0,
            }
            for question, _ in targets
        ],
    }

    updated = 0
    items: list[QuotaRowOut] = []
    for question, target in targets:
        quota = existing.get(question.id)
        if quota is None:
            quota = Quota(
                candidate_id=candidate_id,
                question_id=question.id,
                quota_total=target,
                quota_used=0,
                adjusted_flag=1,
            )
            session.add(quota)
        else:
            quota.quota_total = target
            quota.adjusted_flag = 1
        updated += 1
        items.append(
            QuotaRowOut(
                id=quota.id or 0,
                candidate_id=candidate_id,
                account=candidate.account,
                name=candidate.name,
                question_id=question.id,
                question_code=question.code,
                question_title=question.title,
                quota_total=target,
                quota_used=quota.quota_used or 0,
                quota_remaining=max(target - (quota.quota_used or 0), 0),
                adjusted_flag=True,
                updated_at=quota.updated_at,
            )
        )

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="adjust_quota",
        target_type="quota",
        target_id=f"candidate:{candidate_id}",
        before=before,
        after={
            "candidate_id": candidate_id,
            "account": candidate.account,
            "items": [
                {"question_id": question.id, "question_code": question.code, "quota_total": target}
                for question, target in targets
            ],
            "updated_rows": updated,
            "force_confirm": payload.force_confirm,
        },
        remark=payload.remark or f"按人调整 {candidate.account} 的配额（{updated} 道题）",
        ip=client_ip(request),
    )
    await session.commit()
    return QuotaChangeOut(updated=updated, blocked=len(blocked), items=items)


@router.post("/batch-adjust", response_model=QuotaChangeOut, summary="多人批量加减次数（delta 可正可负）")
async def batch_adjust(
    payload: BatchAdjustIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> QuotaChangeOut:
    question = await session.get(Question, payload.question_id)
    if question is None:
        raise AppError("QUESTION_NOT_FOUND", "题目不存在", status_code=404)

    candidate_ids = list(dict.fromkeys(int(item) for item in payload.candidate_ids))
    blocked_rows = (
        await session.execute(
            select(Candidate.account, Candidate.name, Quota.quota_used, Quota.quota_total)
            .join(Quota, Quota.candidate_id == Candidate.id)
            .where(
                Quota.candidate_id.in_(candidate_ids),
                Quota.question_id == payload.question_id,
                func.greatest(Quota.quota_total + payload.delta, 0) < Quota.quota_used,
            )
            .limit(_MAX_SAMPLES)
        )
    ).all()
    blocked_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Quota)
                .where(
                    Quota.candidate_id.in_(candidate_ids),
                    Quota.question_id == payload.question_id,
                    func.greatest(Quota.quota_total + payload.delta, 0) < Quota.quota_used,
                )
            )
        ).scalar()
        or 0
    )

    if blocked_count and not payload.force_confirm:
        raise AppError(
            "QUOTA_DOWNGRADE_CONFIRM_REQUIRED",
            f"有 {blocked_count} 名考生调整后总次数会低于已用次数，请二次确认",
            status_code=409,
            detail={
                "affected_count": blocked_count,
                "delta": payload.delta,
                "samples": [
                    {
                        "account": account,
                        "name": name,
                        "quota_used": int(used),
                        "quota_total": int(total),
                    }
                    for account, name, used, total in blocked_rows
                ],
            },
        )

    before_distribution = (
        await session.execute(
            select(Quota.quota_total, func.count())
            .where(Quota.candidate_id.in_(candidate_ids), Quota.question_id == payload.question_id)
            .group_by(Quota.quota_total)
        )
    ).all()

    result = await session.execute(
        update(Quota)
        .where(Quota.candidate_id.in_(candidate_ids), Quota.question_id == payload.question_id)
        .values(quota_total=func.greatest(Quota.quota_total + payload.delta, 0), adjusted_flag=1)
    )
    updated = int(result.rowcount or 0)

    await write_audit(
        session,
        admin_id=admin.id,
        admin_username=admin.username,
        action="adjust_quota",
        target_type="quota",
        target_id=f"question:{payload.question_id}",
        before={
            "question_id": payload.question_id,
            "question_code": question.code,
            "candidate_count": len(candidate_ids),
            "quota_total_distribution": {int(total): int(count) for total, count in before_distribution},
        },
        after={
            "question_id": payload.question_id,
            "question_code": question.code,
            "delta": payload.delta,
            "updated_rows": updated,
            "force_confirm": payload.force_confirm,
        },
        remark=payload.remark or f"多人批量{'增加' if payload.delta >= 0 else '减少'} {question.code} 次数 {payload.delta}",
        ip=client_ip(request),
    )
    await session.commit()

    logger.info("多人批量调整：question=%s delta=%s 更新 %s 行", question.code, payload.delta, updated)
    return QuotaChangeOut(updated=updated, blocked=blocked_count)
