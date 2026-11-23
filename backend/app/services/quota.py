"""配额服务：补建配额行、汇总余量。

- 新增题目 → 为全体考生补建该题配额行（INSERT IGNORE，同一事务内）；
- 新增考生 → 为该考生补建所有启用题目的配额行；
- 汇总 → 考生端「总剩余 / 总次数」。

扣减与退还分别在 `services/works.py`（阶段 2）与 `services/handoff*.py` 中实现，
统一走单条 UPDATE ... WHERE quota_used < quota_total，绝不先读后写。
"""

from __future__ import annotations

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Question, Quota


async def backfill_quotas_for_question(session: AsyncSession, question_id: int, quota_total: int) -> int:
    """为全体考生补建某题配额行，返回新插入行数（已存在的行保持不变）。"""
    result = await session.execute(
        text(
            "INSERT IGNORE INTO quotas (candidate_id, question_id, quota_total, quota_used, adjusted_flag) "
            "SELECT c.id, :qid, :total, 0, 0 FROM candidates c"
        ),
        {"qid": int(question_id), "total": int(quota_total)},
    )
    return int(result.rowcount or 0)


async def backfill_quotas_for_candidate(session: AsyncSession, candidate_id: int) -> int:
    """为该考生补建所有启用题目的配额行，返回新插入行数。"""
    result = await session.execute(
        text(
            "INSERT IGNORE INTO quotas (candidate_id, question_id, quota_total, quota_used, adjusted_flag) "
            "SELECT :cid, q.id, q.default_quota, 0, 0 FROM questions q WHERE q.status = 1"
        ),
        {"cid": int(candidate_id)},
    )
    return int(result.rowcount or 0)


async def backfill_quotas_for_candidates(session: AsyncSession, candidate_ids: list[int]) -> int:
    """批量补建：一批新考生的所有启用题目配额行（导入场景）。"""
    if not candidate_ids:
        return 0
    result = await session.execute(
        text(
            "INSERT IGNORE INTO quotas (candidate_id, question_id, quota_total, quota_used, adjusted_flag) "
            "SELECT c.id, q.id, q.default_quota, 0, 0 "
            "FROM candidates c JOIN questions q ON q.status = 1 "
            "WHERE c.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": [int(i) for i in candidate_ids]},
    )
    return int(result.rowcount or 0)


async def candidate_quota_totals(session: AsyncSession, candidate_id: int) -> tuple[int, int]:
    """返回 (总次数, 已用次数)，只统计启用题目的配额行。"""
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Quota.quota_total), 0),
                func.coalesce(func.sum(Quota.quota_used), 0),
            )
            .select_from(Quota)
            .join(Question, Question.id == Quota.question_id)
            .where(Quota.candidate_id == int(candidate_id), Question.status == 1)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)
