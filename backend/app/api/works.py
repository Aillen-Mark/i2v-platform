"""考生端创作接口：新建草稿、提交生成、查询进度、作品列表、安全下载与流式打包。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.deps import get_current_candidate
from app.core.errors import AppError
from app.core.storage import content_disposition, object_exists, presigned_get_url
from app.models import Candidate, Question, Work
from app.schemas.download import DownloadUrlOut, MyWorksOut, QuestionGroupOut, WorkItemOut
from app.schemas.work import WORK_STATUS_TEXT, SubmitResultOut, WorkOut
from app.services import works as works_svc
from app.services.download import (
    DOWNLOAD_TTL_SECONDS,
    build_video_filename,
    build_zip_filename,
    stream_zip,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["考生端 · 创作"])

settings = get_settings()


async def _image_url(work: Work) -> str | None:
    if not work.image_key:
        return None
    return await asyncio.to_thread(
        presigned_get_url, settings.MINIO_BUCKET_PUBLIC, work.image_key, works_svc.IMAGE_URL_TTL
    )


async def _video_url(work: Work) -> str | None:
    if not work.video_key:
        return None
    return await asyncio.to_thread(
        presigned_get_url, settings.MINIO_BUCKET_VIDEOS, work.video_key, works_svc.IMAGE_URL_TTL
    )


async def _to_work_out(work: Work, queue: dict[str, int] | None = None) -> WorkOut:
    queue = queue or {}
    return WorkOut(
        id=work.id,
        task_no=work.task_no,
        candidate_id=work.candidate_id,
        question_id=work.question_id,
        seq=work.seq,
        prompt=work.prompt,
        aspect_ratio=work.aspect_ratio,
        task_status=work.task_status,
        status_text=WORK_STATUS_TEXT.get(work.task_status, work.task_status),
        engine_code=work.engine_code,
        image_url=await _image_url(work),
        video_url=await _video_url(work),
        error_code=work.error_code,
        error_msg=work.error_msg,
        submitted_at=work.submitted_at,
        finished_at=work.finished_at,
        created_at=work.created_at,
        queue_ahead=queue.get("queue_ahead"),
        estimated_wait_sec=queue.get("estimated_wait_sec"),
        quota_remaining=queue.get("quota_remaining"),
    )


@router.post("/works", response_model=WorkOut, summary="新建作品草稿（上传首帧图 + 提示词）")
async def create_work(
    question_id: int = Form(..., description="题目 ID"),
    prompt: str = Form(..., description="提示词，1~300 字"),
    image: UploadFile = File(..., description="首帧图 JPG / PNG / WEBP，≤10MB"),
    aspect_ratio: str = Form("16:9", description="画幅比例，默认 16:9"),
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> WorkOut:
    data = await image.read()
    work = await works_svc.create_draft(
        session,
        candidate,
        question_id=question_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        data=data,
        filename=image.filename or "upload",
        content_type=image.content_type,
    )
    return await _to_work_out(work)


@router.post("/works/{work_id}/generate", response_model=SubmitResultOut, summary="提交生成（5 步校验链 + 原子扣配额）")
async def generate_work(
    work_id: int,
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> SubmitResultOut:
    result = await works_svc.submit_work(session, candidate, work_id)
    work = result["work"]
    idempotent = bool(result["idempotent"])
    return SubmitResultOut(
        id=work.id,
        task_no=work.task_no,
        task_status=work.task_status,
        status_text=WORK_STATUS_TEXT.get(work.task_status, work.task_status),
        seq=work.seq,
        engine_code=work.engine_code,
        quota_remaining=result.get("quota_remaining"),
        queue_ahead=result.get("queue_ahead"),
        estimated_wait_sec=result.get("estimated_wait_sec"),
        idempotent=idempotent,
        message="该作品已提交过，未重复扣减次数" if idempotent else "已提交生成",
    )


@router.get("/works/{work_id}", response_model=WorkOut, summary="查询作品进度与排队位次")
async def get_work(
    work_id: int,
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> WorkOut:
    work = (
        await session.execute(
            select(Work).where(Work.id == int(work_id), Work.candidate_id == candidate.id)
        )
    ).scalar_one_or_none()
    if work is None:
        raise AppError("WORK_NOT_FOUND", "作品不存在或不属于当前考生", status_code=404)

    queue: dict[str, int] = {}
    if work.task_status == "queued":
        ahead = await works_svc.queue_ahead(work.id)
        avg_cost = await works_svc.average_cost_seconds()
        engine = await works_svc.get_default_engine(session)
        queue["queue_ahead"] = ahead
        queue["estimated_wait_sec"] = works_svc.estimate_wait_seconds(
            ahead, engine["max_concurrency"], avg_cost
        )
    return await _to_work_out(work, queue)


# --------------------------------------------------------------------------- #
# 作品列表 / 安全下载 / 打包下载
# --------------------------------------------------------------------------- #
async def _to_item(work: Work, question: Question | None, preview: str | None) -> WorkItemOut:
    return WorkItemOut(
        id=work.id,
        task_no=work.task_no,
        question_id=work.question_id,
        question_code=question.code if question else "",
        question_title=question.title if question else "",
        seq=work.seq,
        task_status=work.task_status,
        status_text=WORK_STATUS_TEXT.get(work.task_status, work.task_status),
        prompt=work.prompt,
        engine_code=work.engine_code,
        error_code=work.error_code,
        error_msg=work.error_msg,
        duration_sec=work.duration_sec,
        file_size=work.file_size,
        created_at=work.created_at,
        submitted_at=work.submitted_at,
        finished_at=work.finished_at,
        preview_url=preview,
        download_ready=bool(work.task_status == "succeeded" and work.video_key),
    )


@router.get("/me/works", response_model=MyWorksOut, summary="我的作品（按题目分组 + 平铺列表）")
async def list_my_works(
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> MyWorksOut:
    works = (
        await session.execute(
            select(Work)
            .where(Work.candidate_id == candidate.id)
            .order_by(Work.created_at.desc(), Work.id.desc())
        )
    ).scalars().all()

    questions = {
        question.id: question
        for question in (await session.execute(select(Question))).scalars().all()
    }

    items: list[WorkItemOut] = []
    groups: dict[int, QuestionGroupOut] = {}
    for work in works:
        question = questions.get(work.question_id)
        preview = None
        if work.task_status == "succeeded" and work.video_key:
            preview = await asyncio.to_thread(
                presigned_get_url,
                settings.MINIO_BUCKET_VIDEOS,
                work.video_key,
                DOWNLOAD_TTL_SECONDS,
            )
        item = await _to_item(work, question, preview)
        items.append(item)
        group = groups.get(work.question_id)
        if group is None:
            group = QuestionGroupOut(
                question_id=work.question_id,
                question_code=question.code if question else "",
                question_title=question.title if question else "",
                total=0,
                works=[],
            )
            groups[work.question_id] = group
        group.works.append(item)
        group.total += 1

    return MyWorksOut(total=len(items), groups=list(groups.values()), items=items)


async def _resolve_download(work_id: int, candidate: Candidate, session: AsyncSession):
    """校验归属/状态/对象存在性，返回 (work, question, filename, size)。"""
    work = (
        await session.execute(
            select(Work).where(Work.id == int(work_id), Work.candidate_id == candidate.id)
        )
    ).scalar_one_or_none()
    if work is None:
        raise AppError("WORK_NOT_FOUND", "作品不存在或不属于当前考生", status_code=404)
    if work.task_status != "succeeded":
        raise AppError(
            "WORK_NOT_READY",
            f"作品尚未出片（当前状态：{WORK_STATUS_TEXT.get(work.task_status, work.task_status)}），暂不能下载",
            status_code=409,
        )
    if not work.video_key:
        raise AppError("VIDEO_MISSING", "该作品没有成片记录", status_code=404)

    exists = await asyncio.to_thread(object_exists, settings.MINIO_BUCKET_VIDEOS, work.video_key)
    if not exists:
        raise AppError(
            "VIDEO_OBJECT_MISSING",
            "成片对象在存储中不存在（本地 Mock 只写记录不写文件），请等待真实 Worker 产出",
            status_code=404,
            detail={"video_key": work.video_key},
        )

    question = await session.get(Question, work.question_id)
    filename = build_video_filename(
        candidate.account,
        question.code if question else f"Q{work.question_id}",
        work.seq,
        work.finished_at,
    )
    # 文件大小优先取 works.file_size（出片时已写入，省一次 S3 HEAD 往返）；
    # 历史数据缺失时才回源对象存储核对。
    size = work.file_size
    if not size:
        from app.core.storage import object_size

        size = await asyncio.to_thread(object_size, settings.MINIO_BUCKET_VIDEOS, work.video_key)
    return work, question, filename, size


@router.get("/works/{work_id}/download", response_model=DownloadUrlOut, summary="签发成片下载链接（30 分钟）")
async def download_work(
    work_id: int,
    redirect: bool = Query(False, description="true 则直接 302 跳转到签名地址"),
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
):
    work, _question, filename, size = await _resolve_download(work_id, candidate, session)
    url = await asyncio.to_thread(
        presigned_get_url,
        settings.MINIO_BUCKET_VIDEOS,
        work.video_key,
        DOWNLOAD_TTL_SECONDS,
        filename=filename,
        content_type="video/mp4",
    )
    logger.info("考生 %s 下载作品 %s：%s", candidate.account, work.task_no, filename)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadUrlOut(
        download_url=url,
        filename=filename,
        expires_in=DOWNLOAD_TTL_SECONDS,
        file_size=size,
    )


@router.get("/me/download-all", summary="打包下载本人全部已出片作品（流式 zip）")
async def download_all(
    candidate: Candidate = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    works = (
        await session.execute(
            select(Work)
            .where(Work.candidate_id == candidate.id, Work.task_status == "succeeded")
            .order_by(Work.id.asc())
        )
    ).scalars().all()

    questions = {
        question.id: question
        for question in (await session.execute(select(Question))).scalars().all()
    }

    entries: list[tuple[str, str]] = []
    for work in works:
        if not work.video_key:
            continue
        if not await asyncio.to_thread(object_exists, settings.MINIO_BUCKET_VIDEOS, work.video_key):
            logger.warning("作品 %s 的成片对象不存在，跳过打包：%s", work.task_no, work.video_key)
            continue
        question = questions.get(work.question_id)
        entries.append(
            (
                build_video_filename(
                    candidate.account,
                    question.code if question else f"Q{work.question_id}",
                    work.seq,
                    work.finished_at,
                ),
                work.video_key,
            )
        )

    if not entries:
        raise AppError("NO_VIDEO_TO_DOWNLOAD", "暂无可下载的已出片作品", status_code=400)

    zip_name = build_zip_filename(candidate.account)
    logger.info("考生 %s 打包下载 %s 个成片：%s", candidate.account, len(entries), zip_name)
    return StreamingResponse(
        stream_zip(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(zip_name),
            "X-Video-Count": str(len(entries)),
        },
    )

