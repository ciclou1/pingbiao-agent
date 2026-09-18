"""专家问答与横向对比路由（PRD 3.5）。

- 问答：检索增强 + 强制引文，记录留痕
- 对比：条目 × 投标人响应矩阵（审查结论是委员会共同结论，成员可见）；
        评分对比含他人分数，仅组长/admin 可见（汇总前专家间隔离）
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import get_current_user
from ..database import get_db
from ..models import (
    Bidder,
    ProjectMember,
    QaMessage,
    RequirementItem,
    ReviewResult,
    Score,
    User,
)
from ..qa import answer_question, fetch_chunks
from .deps import require_project, require_project_manager, require_project_member

router = APIRouter(prefix="/projects/{project_id}", tags=["qa"])

REVIEW_TYPES = ("qualification", "compliance")


class AskIn(BaseModel):
    question: str
    scope: str = "all"  # all / tender / bidder
    bidder_id: int | None = None


def _qa_out(m: QaMessage) -> dict:
    return {
        "id": m.id,
        "username": m.username,
        "scope": m.scope,
        "bidder_id": m.bidder_id,
        "question": m.question,
        "answer": m.answer,
        "citations": m.citations or [],
        "insufficient": m.insufficient,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.post("/qa/ask")
async def ask(
    project_id: int,
    body: AskIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if len(question) > 500:
        raise HTTPException(400, "问题过长（限 500 字）")
    if body.scope not in ("all", "tender", "bidder"):
        raise HTTPException(400, "scope 必须是 all/tender/bidder")
    if body.scope == "bidder":
        if body.bidder_id is None:
            raise HTTPException(400, "按投标人提问时必须指定 bidder_id")
        bidder = await db.get(Bidder, body.bidder_id)
        if bidder is None or bidder.project_id != project_id:
            raise HTTPException(400, "bidder_id 无效")

    chunks = await fetch_chunks(db, project_id, body.scope, body.bidder_id)
    # 混合检索（关键词 + 向量）：向量服务不可用时自动退化为纯关键词
    from ..retriever import build_index, hybrid_retrieve
    from ..synonyms import expand

    index = build_index(chunks)

    async def retrieve_fn(query: str, k: int) -> list[dict]:
        return await hybrid_retrieve(
            db,
            index,
            query,
            project_id=project_id,
            scope=body.scope,
            bidder_id=body.bidder_id,
            top_k=k,
            expand_fn=expand,
        )

    result = await answer_question(retrieve_fn, question, has_material=bool(chunks))

    msg = QaMessage(
        project_id=project_id,
        user_id=user.id,
        username=user.username,
        bidder_id=body.bidder_id if body.scope == "bidder" else None,
        scope=body.scope,
        question=question,
        answer=result["answer"],
        citations=result["citations"],
        insufficient=result["insufficient"],
        model=result["model"],
    )
    db.add(msg)
    await db.flush()
    await audit(
        db,
        action="qa_ask",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"scope": body.scope, "bidder_id": body.bidder_id, "qa_id": msg.id},
    )
    await db.commit()
    return _qa_out(msg)


@router.get("/qa/history")
async def history(
    project_id: int,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    rows = await db.execute(
        select(QaMessage)
        .where(QaMessage.project_id == project_id)
        .order_by(QaMessage.id.desc())
        .limit(min(max(limit, 1), 200))
    )
    return {"messages": [_qa_out(m) for m in reversed(list(rows.scalars()))]}


# ---------------------------------------------------------------------------
# 横向对比
# ---------------------------------------------------------------------------

@router.get("/compare/matrix")
async def compare_matrix(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """条目 × 投标人 响应矩阵（用专家终值优先，未定时显示 AI 结论）。"""
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)

    bidders = list((await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))).scalars())
    items = list(
        (
            await db.execute(
                select(RequirementItem)
                .where(
                    RequirementItem.project_id == project_id,
                    RequirementItem.item_type.in_(REVIEW_TYPES),
                    RequirementItem.expert_confirmed.is_(True),
                )
                .order_by(RequirementItem.item_type, RequirementItem.sort_order, RequirementItem.id)
            )
        ).scalars()
    )
    results = {
        (r.item_id, r.bidder_id): r
        for r in (
            await db.execute(select(ReviewResult).where(ReviewResult.project_id == project_id))
        ).scalars()
    }

    rows = []
    for it in items:
        cells = []
        for b in bidders:
            r = results.get((it.id, b.id))
            cells.append(
                {
                    "bidder_id": b.id,
                    "state": (r.expert_state or r.ai_state) if r else None,
                    "ai_state": r.ai_state if r else None,
                    "expert_state": r.expert_state if r else None,
                    "decided": bool(r and r.expert_state),
                }
            )
        rows.append(
            {
                "item": {
                    "id": it.id,
                    "item_type": it.item_type,
                    "category": it.category,
                    "clause_text": it.clause_text,
                    "requirement": it.requirement,
                    "source_page": it.source_page,
                },
                "cells": cells,
            }
        )

    summary = []
    for b in bidders:
        states = [c["state"] for row in rows for c in row["cells"] if c["bidder_id"] == b.id]
        summary.append(
            {
                "bidder_id": b.id,
                "bidder_name": b.name,
                "passed": sum(1 for s in states if s == "passed"),
                "failed": sum(1 for s in states if s == "failed"),
                "review": sum(1 for s in states if s == "review"),
                "pending": sum(1 for s in states if s is None),
            }
        )
    return {
        "bidders": [{"id": b.id, "name": b.name} for b in bidders],
        "rows": rows,
        "summary": summary,
    }


@router.get("/compare/scores")
async def compare_scores(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """评分对比：各投标人分项均分与总分（含他人分数 → 仅组长/admin）。"""
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)

    items = list(
        (
            await db.execute(
                select(RequirementItem)
                .where(
                    RequirementItem.project_id == project_id,
                    RequirementItem.item_type == "scoring",
                    RequirementItem.expert_confirmed.is_(True),
                )
                .order_by(RequirementItem.sort_order, RequirementItem.id)
            )
        ).scalars()
    )
    bidders = list((await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))).scalars())
    score_rows = list((await db.execute(select(Score).where(Score.project_id == project_id))).scalars())

    experts_total = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.member_role.in_(("expert", "leader"))
            )
        )
    ).scalars().all()

    grouped: dict[tuple[int, int], list[Decimal]] = {}
    for s in score_rows:
        if s.score is not None:
            grouped.setdefault((s.item_id, s.bidder_id), []).append(s.score)

    def avg(vals: list[Decimal]) -> float | None:
        return round(float(sum(vals) / len(vals)), 2) if vals else None

    out = []
    for b in bidders:
        per_item = []
        scored_items = 0
        total = 0.0
        for it in items:
            vals = grouped.get((it.id, b.id), [])
            a = avg(vals)
            if a is not None:
                total += a
                scored_items += 1
            per_item.append(
                {
                    "item_id": it.id,
                    "category": it.category,
                    "clause_text": it.clause_text,
                    "max_score": float(it.score) if it.score is not None else None,
                    "average": a,
                    "scored_experts": len(vals),
                }
            )
        out.append(
            {
                "bidder_id": b.id,
                "bidder_name": b.name,
                "items": per_item,
                "total_average": round(total, 2),
                "scored_items": scored_items,
            }
        )

    return {
        "items": [
            {"id": it.id, "category": it.category, "clause_text": it.clause_text, "max_score": float(it.score) if it.score is not None else None}
            for it in items
        ],
        "bidders": out,
        "experts_total": len(experts_total),
    }
