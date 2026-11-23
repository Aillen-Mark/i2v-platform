"""5 维度用量统计 + CSV 导出 + 成片下载清单。

红线（验收标准 #11）：所有数字必须与 `works` / `candidates` / `questions` / `engines`
真实表逐笔对账一致；费用严格按 `SUM(duration_sec × price_per_sec)` 折算。
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import get_current_admin
from app.core.errors import AppError
from app.core.storage import presigned_get_url
from app.models import Admin, Candidate, Engine, Question, Quota, Work
from app.schemas.stats import (
    CandidateStatOut,
    CandidateStatPageOut,
    DayStatOut,
    EngineStatOut,
    OverviewOut,
    QuestionStatOut,
    VideoLinkOut,
)
from app.services.download import DOWNLOAD_TTL_SECONDS, build_video_filename

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/stats", tags=["管理端 · 用量统计"])

settings = get_settings()

PENDING_STATUSES = ("queued", "running")
CSV_BOM = "\ufeff"


def _rate(part: int, total: int) -> float:
    return round(part / total * 100, 2) if total else 0.0


def _csv_response(filename: str, rows: list[list[object]]) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    content = (CSV_BOM + buffer.getvalue()).encode("utf-8")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/overview", response_model=OverviewOut, summary="总览卡片")
async def overview(
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> OverviewOut:
    candidates_total = int(
        (await session.execute(select(func.count()).select_from(Candidate))).scalar() or 0
    )
    works_total = int(
        (
            await session.execute(
                select(func.count()).select_from(Work).where(Work.task_status != "draft")
            )
        ).scalar()
        or 0
    )
    succeeded = int(
        (
            await session.execute(
                select(func.count()).select_from(Work).where(Work.task_status == "succeeded")
            )
        ).scalar()
        or 0
    )
    failed = int(
        (
            await session.execute(
                select(func.count()).select_from(Work).where(Work.task_status == "failed")
            )
        ).scalar()
        or 0
    )
    pending = int(
        (
            await session.execute(
                select(func.count()).select_from(Work).where(Work.task_status.in_(PENDING_STATUSES))
            )
        ).scalar()
        or 0
    )
    total_duration = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Work.duration_sec), 0)).where(Work.task_status == "succeeded")
            )
        ).scalar()
        or 0
    )
    cost = (
        await session.execute(
            select(func.coalesce(func.sum(Work.duration_sec * Engine.price_per_sec), 0))
            .select_from(Work)
            .join(Engine, Engine.code == Work.engine_code)
            .where(Work.task_status == "succeeded")
        )
    ).scalar() or Decimal("0")

    return OverviewOut(
        candidates_total=candidates_total,
        works_total=works_total,
        succeeded=succeeded,
        failed=failed,
        pending=pending,
        success_rate=_rate(succeeded, works_total),
        total_duration_sec=total_duration,
        estimated_cost=Decimal(str(cost)).quantize(Decimal("0.0001")),
    )


@router.get("/by-candidate", response_model=CandidateStatPageOut, summary="按考生统计（分页）")
async def by_candidate(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    keyword: str | None = Query(None, description="按账号/姓名模糊搜索"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> CandidateStatPageOut:
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append((Candidate.account.like(like)) | (Candidate.name.like(like)))

    total = int(
        (
            await session.execute(select(func.count()).select_from(Candidate).where(*conditions))
        ).scalar()
        or 0
    )

    quota_sub = (
        select(
            Quota.candidate_id.label("candidate_id"),
            func.coalesce(func.sum(Quota.quota_total), 0).label("quota_total"),
            func.coalesce(func.sum(Quota.quota_used), 0).label("quota_used"),
        )
        .join(Question, Question.id == Quota.question_id)
        .where(Question.status == 1)
        .group_by(Quota.candidate_id)
        .subquery()
    )
    work_sub = (
        select(
            Work.candidate_id.label("candidate_id"),
            func.sum(case((Work.task_status == "succeeded", 1), else_=0)).label("success_count"),
            func.sum(case((Work.task_status == "failed", 1), else_=0)).label("failed_count"),
            func.max(Work.submitted_at).label("last_active_at"),
        )
        .group_by(Work.candidate_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(
                Candidate,
                func.coalesce(quota_sub.c.quota_total, 0),
                func.coalesce(quota_sub.c.quota_used, 0),
                func.coalesce(work_sub.c.success_count, 0),
                func.coalesce(work_sub.c.failed_count, 0),
                work_sub.c.last_active_at,
            )
            .outerjoin(quota_sub, quota_sub.c.candidate_id == Candidate.id)
            .outerjoin(work_sub, work_sub.c.candidate_id == Candidate.id)
            .where(*conditions)
            .order_by(Candidate.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    items = [
        CandidateStatOut(
            candidate_id=candidate.id,
            account=candidate.account,
            name=candidate.name,
            status=candidate.status,
            quota_total=int(quota_total or 0),
            quota_used=int(quota_used or 0),
            quota_remaining=max(int(quota_total or 0) - int(quota_used or 0), 0),
            success_count=int(success_count or 0),
            failed_count=int(failed_count or 0),
            last_active_at=last_active,
        )
        for candidate, quota_total, quota_used, success_count, failed_count, last_active in rows
    ]
    return CandidateStatPageOut(total=total, page=page, size=size, items=items)


@router.get("/by-question", response_model=list[QuestionStatOut], summary="按题目统计")
async def by_question(
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[QuestionStatOut]:
    questions = (
        await session.execute(select(Question).order_by(Question.sort_order.asc(), Question.id.asc()))
    ).scalars().all()

    rows = (
        await session.execute(
            select(
                Work.question_id,
                func.count().label("total"),
                func.sum(case((Work.task_status == "succeeded", 1), else_=0)).label("succeeded"),
                func.sum(case((Work.task_status == "failed", 1), else_=0)).label("failed"),
                func.avg(Work.cost_seconds).label("avg_cost"),
            )
            .where(Work.task_status != "draft")
            .group_by(Work.question_id)
        )
    ).all()
    stats = {
        question_id: (int(total), int(succeeded or 0), int(failed or 0), float(avg or 0) if avg else None)
        for question_id, total, succeeded, failed, avg in rows
    }

    result: list[QuestionStatOut] = []
    for question in questions:
        total, succeeded, failed, avg_cost = stats.get(question.id, (0, 0, 0, None))
        result.append(
            QuestionStatOut(
                question_id=question.id,
                code=question.code,
                title=question.title,
                status=int(question.status),
                total=total,
                succeeded=succeeded,
                failed=failed,
                fail_rate=_rate(failed, total),
                avg_cost_sec=round(avg_cost, 2) if avg_cost is not None else None,
            )
        )
    return result


@router.get("/by-engine", response_model=list[EngineStatOut], summary="按模型引擎统计")
async def by_engine(
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[EngineStatOut]:
    engines = (await session.execute(select(Engine).order_by(Engine.id.asc()))).scalars().all()

    rows = (
        await session.execute(
            select(
                Work.engine_code,
                func.count().label("calls"),
                func.sum(case((Work.task_status == "succeeded", 1), else_=0)).label("succeeded"),
                func.coalesce(
                    func.sum(case((Work.task_status == "succeeded", Work.duration_sec), else_=0)), 0
                ).label("duration"),
            )
            .where(Work.task_status != "draft", Work.engine_code.is_not(None))
            .group_by(Work.engine_code)
        )
    ).all()
    per_engine = {
        code: (int(calls), int(succeeded or 0), int(duration or 0))
        for code, calls, succeeded, duration in rows
    }
    total_calls = sum(value[0] for value in per_engine.values())

    result: list[EngineStatOut] = []
    for engine in engines:
        calls, succeeded, duration = per_engine.get(engine.code, (0, 0, 0))
        price = Decimal(str(engine.price_per_sec or 0))
        result.append(
            EngineStatOut(
                code=engine.code,
                name=engine.name,
                resolution=engine.resolution,
                calls=calls,
                succeeded=succeeded,
                total_duration_sec=duration,
                share_rate=_rate(calls, total_calls),
                cost=(price * duration).quantize(Decimal("0.0001")),
            )
        )
    return result


@router.get("/by-day", response_model=list[DayStatOut], summary="按天趋势统计")
async def by_day(
    days: int = Query(30, ge=1, le=365, description="回溯天数"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[DayStatOut]:
    day_col = func.date(Work.submitted_at)
    rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.count().label("total"),
                func.sum(case((Work.task_status == "succeeded", 1), else_=0)).label("succeeded"),
                func.sum(case((Work.task_status == "failed", 1), else_=0)).label("failed"),
            )
            .where(Work.task_status != "draft", Work.submitted_at.is_not(None))
            .group_by(day_col)
            .order_by(day_col.asc())
        )
    ).all()

    result: list[DayStatOut] = []
    for day, total, succeeded, failed in rows[-days:]:
        succeeded = int(succeeded or 0)
        failed = int(failed or 0)
        total = int(total or 0)
        result.append(
            DayStatOut(
                date=day.isoformat() if isinstance(day, (date, datetime)) else str(day),
                total=total,
                succeeded=succeeded,
                failed=failed,
                fail_rate=_rate(failed, total),
            )
        )
    return result


@router.get("/export-csv", summary="导出明细 CSV（UTF-8 BOM，Excel 直开不乱码）")
async def export_csv(
    dimension: str = Query("works", pattern="^(works|candidates)$"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if dimension == "works":
        rows = (
            await session.execute(
                select(
                    Work.id,
                    Work.task_no,
                    Candidate.account,
                    Candidate.name,
                    Question.code,
                    Work.seq,
                    Work.task_status,
                    Work.engine_code,
                    Work.duration_sec,
                    Work.cost_seconds,
                    Work.error_code,
                    Work.submitted_at,
                    Work.finished_at,
                )
                .join(Candidate, Candidate.id == Work.candidate_id)
                .join(Question, Question.id == Work.question_id)
                .order_by(Work.id.asc())
            )
        ).all()
        header = [
            "作品ID", "任务号", "考生账号", "考生姓名", "题号", "第几次",
            "状态", "引擎", "时长(秒)", "生成耗时(秒)", "错误码", "提交时间", "完成时间",
        ]
        body = [
            [
                row[0], row[1], row[2], row[3], row[4], row[5],
                row[6], row[7], row[8], row[9], row[10],
                row[11].strftime("%Y-%m-%d %H:%M:%S") if row[11] else "",
                row[12].strftime("%Y-%m-%d %H:%M:%S") if row[12] else "",
            ]
            for row in rows
        ]
        return _csv_response(f"works_{stamp}.csv", [header, *body])

    rows = (
        await session.execute(
            select(
                Candidate.account,
                Candidate.name,
                Candidate.status,
                func.coalesce(func.sum(Quota.quota_total), 0),
                func.coalesce(func.sum(Quota.quota_used), 0),
            )
            .outerjoin(Quota, Quota.candidate_id == Candidate.id)
            .group_by(Candidate.id)
            .order_by(Candidate.id.asc())
        )
    ).all()
    header = ["考生账号", "考生姓名", "状态", "总配额", "已用次数", "剩余次数"]
    body = [
        [row[0], row[1], "启用" if row[2] == 1 else "禁用", int(row[3]), int(row[4]), max(int(row[3]) - int(row[4]), 0)]
        for row in rows
    ]
    return _csv_response(f"candidates_{stamp}.csv", [header, *body])


@router.get("/export-video-links", response_model=None, summary="成片下载清单（规范文件名 + 30 分钟签名直链）")
async def export_video_links(
    question_id: int | None = Query(None, gt=0),
    candidate_id: int | None = Query(None, gt=0),
    fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    conditions = [Work.task_status == "succeeded", Work.video_key.is_not(None)]
    if question_id is not None:
        conditions.append(Work.question_id == question_id)
    if candidate_id is not None:
        conditions.append(Work.candidate_id == candidate_id)

    rows = (
        await session.execute(
            select(Work, Candidate.account, Question.code)
            .join(Candidate, Candidate.id == Work.candidate_id)
            .join(Question, Question.id == Work.question_id)
            .where(*conditions)
            .order_by(Work.id.asc())
        )
    ).all()

    items: list[VideoLinkOut] = []
    for work, account, question_code in rows:
        filename = build_video_filename(account, question_code, work.seq, work.finished_at)
        url = presigned_get_url(
            settings.MINIO_BUCKET_VIDEOS,
            work.video_key,
            DOWNLOAD_TTL_SECONDS,
            filename=filename,
            content_type="video/mp4",
        )
        items.append(
            VideoLinkOut(
                filename=filename,
                account=account,
                question_code=question_code,
                download_url=url,
                expires_in=DOWNLOAD_TTL_SECONDS,
            )
        )

    if fmt == "csv":
        header = ["规范文件名", "考生账号", "题号", "签名下载直链", "有效期(秒)"]
        body = [[item.filename, item.account, item.question_code, item.download_url, item.expires_in] for item in items]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return _csv_response(f"video_links_{stamp}.csv", [header, *body])

    return items
