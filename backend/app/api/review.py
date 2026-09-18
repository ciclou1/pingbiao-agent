"""审查与评分路由（PRD 3.3 / 3.4）：三态结论、专家终值、打分与隔离、组长汇总。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import get_current_user
from ..database import get_db
from ..models import (
    Bidder,
    ProjectMember,
    RequirementItem,
    ReviewResult,
    Score,
    ScoreHistory,
    ScoreSuggestion,
    User,
)
from ..worker import enqueue
from .deps import require_project, require_project_manager, require_project_member

router = APIRouter(prefix="/projects/{project_id}", tags=["review"])

STATES = ("passed", "failed", "review")
REVIEW_TYPES = ("qualification", "compliance")


class RunIn(BaseModel):
    bidder_id: int | None = None


class ResultPatchIn(BaseModel):
    expert_state: str
    expert_reason: str = ""


class ScoreIn(BaseModel):
    item_id: int
    bidder_id: int
    score: float | None = None
    comment: str = ""
    reason: str = ""  # 改分理由（留痕）


def _result_out(r: ReviewResult | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": r.id,
        "item_id": r.item_id,
        "bidder_id": r.bidder_id,
        "ai_state": r.ai_state,
        "ai_reason": r.ai_reason,
        "ai_evidence": r.ai_evidence or [],
        "ai_confidence": r.ai_confidence,
        "ai_check": r.ai_check or {},
        "expert_state": r.expert_state,
        "expert_reason": r.expert_reason,
        "expert_decided_at": r.expert_decided_at.isoformat() if r.expert_decided_at else None,
    }


def _item_out(it: RequirementItem) -> dict:
    return {
        "id": it.id,
        "item_type": it.item_type,
        "category": it.category,
        "clause_text": it.clause_text,
        "requirement": it.requirement,
        "score": float(it.score) if it.score is not None else None,
        "source_page": it.source_page,
        "expert_confirmed": it.expert_confirmed,
    }


@router.post("/review/run")
async def run_review(
    project_id: int,
    body: RunIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """对指定投标人（或全部投标人）跑审查管线，每个投标人一个任务。"""
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)

    confirmed = (
        await db.execute(
            select(func.count())
            .select_from(RequirementItem)
            .where(RequirementItem.project_id == project_id, RequirementItem.expert_confirmed.is_(True))
        )
    ).scalar_one()
    if not confirmed:
        raise HTTPException(400, "还没有已确认的评审条目，请先在条目库确认后再审查")

    if body.bidder_id is not None:
        bidder = await db.get(Bidder, body.bidder_id)
        if bidder is None or bidder.project_id != project_id:
            raise HTTPException(400, "bidder_id 无效")
        bidders = [bidder]
    else:
        bidders = list(
            (await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))).scalars()
        )
    if not bidders:
        raise HTTPException(400, "该项目还没有投标人，请先添加投标人并上传投标文件")

    job_ids = []
    for b in bidders:
        job = await enqueue(db, "review_bidder", {"project_id": project_id, "bidder_id": b.id}, created_by=user.id)
        job_ids.append({"job_id": job.id, "bidder_id": b.id, "bidder_name": b.name})
    await audit(
        db,
        action="review_run",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"bidders": [b.id for b in bidders]},
    )
    await db.commit()
    return {"jobs": job_ids}


@router.get("/review/matrix")
async def review_matrix(
    project_id: int,
    bidder_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """审查工作台数据：条目列表 + 该投标人的 AI 结论与专家终值。"""
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    bidder = await db.get(Bidder, bidder_id)
    if bidder is None or bidder.project_id != project_id:
        raise HTTPException(404, "投标人不存在")

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
        r.item_id: r
        for r in (
            await db.execute(
                select(ReviewResult).where(
                    ReviewResult.project_id == project_id, ReviewResult.bidder_id == bidder_id
                )
            )
        ).scalars()
    }

    rows = [{"item": _item_out(it), "result": _result_out(results.get(it.id))} for it in items]
    summary = {"passed": 0, "failed": 0, "review": 0, "pending": 0, "total": len(rows)}
    for r in rows:
        res = r["result"]
        if res is None:
            summary["pending"] += 1
        else:
            summary[res["expert_state"] or res["ai_state"]] = (
                summary.get(res["expert_state"] or res["ai_state"], 0) + 1
            )
    return {"bidder": {"id": bidder.id, "name": bidder.name}, "rows": rows, "summary": summary}


@router.patch("/review/results/{result_id}")
async def patch_result(
    project_id: int,
    result_id: int,
    body: ResultPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """专家/组长确认终值。审查结论是评标委员会共同结论，不隔离（隔离的是打分）。"""
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    if body.expert_state not in STATES:
        raise HTTPException(400, f"expert_state 必须是 {'/'.join(STATES)}")
    r = await db.get(ReviewResult, result_id)
    if r is None or r.project_id != project_id:
        raise HTTPException(404, "审查结论不存在")
    if body.expert_state == "failed" and not body.expert_reason.strip():
        raise HTTPException(400, "判定不通过必须填写理由（引用招标文件条款）")
    r.expert_state = body.expert_state
    r.expert_reason = body.expert_reason.strip()
    r.expert_decided_by = user.id
    r.expert_decided_at = datetime.now(timezone.utc)
    r.updated_at = r.expert_decided_at
    await audit(
        db,
        action="review_verdict",
        user_id=user.id,
        username=user.username,
        target_type="review_result",
        target_id=r.id,
        detail={"state": body.expert_state, "was": r.ai_state},
    )
    await db.commit()
    return _result_out(r)


# ---------------------------------------------------------------------------
# 评分（专家间汇总前互相不可见）
# ---------------------------------------------------------------------------

@router.get("/score/sheet")
async def score_sheet(
    project_id: int,
    bidder_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """打分表：只返回当前专家自己的分数（隔离），并附 AI 建议供参考。"""
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    bidder = await db.get(Bidder, bidder_id)
    if bidder is None or bidder.project_id != project_id:
        raise HTTPException(404, "投标人不存在")

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
    suggestions = {
        s.item_id: s
        for s in (
            await db.execute(
                select(ScoreSuggestion).where(
                    ScoreSuggestion.project_id == project_id, ScoreSuggestion.bidder_id == bidder_id
                )
            )
        ).scalars()
    }
    my_scores = {
        s.item_id: s
        for s in (
            await db.execute(
                select(Score).where(
                    Score.project_id == project_id, Score.bidder_id == bidder_id, Score.expert_id == user.id
                )
            )
        ).scalars()
    }

    rows = []
    for it in items:
        s = suggestions.get(it.id)
        mine = my_scores.get(it.id)
        rows.append(
            {
                "item": _item_out(it),
                "suggestion": None
                if s is None
                else {
                    "summary": s.summary,
                    "suggested_score": float(s.suggested_score) if s.suggested_score is not None else None,
                    "range_low": float(s.range_low) if s.range_low is not None else None,
                    "range_high": float(s.range_high) if s.range_high is not None else None,
                    "confidence": s.confidence,
                    "evidence": s.evidence or [],
                },
                "my_score": None
                if mine is None
                else {
                    "score": float(mine.score) if mine.score is not None else None,
                    "comment": mine.comment,
                    "updated_at": mine.updated_at.isoformat() if mine.updated_at else None,
                },
            }
        )
    scored = sum(1 for r in rows if r["my_score"] is not None)
    return {
        "bidder": {"id": bidder.id, "name": bidder.name},
        "rows": rows,
        "my_progress": {"scored": scored, "total": len(rows)},
    }


@router.put("/score")
async def put_score(
    project_id: int,
    body: ScoreIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)

    item = await db.get(RequirementItem, body.item_id)
    if item is None or item.project_id != project_id or item.item_type != "scoring":
        raise HTTPException(400, "评分项无效")
    bidder = await db.get(Bidder, body.bidder_id)
    if bidder is None or bidder.project_id != project_id:
        raise HTTPException(400, "投标人无效")
    if body.score is not None:
        if body.score < 0:
            raise HTTPException(400, "分数不能为负")
        if item.score is not None and body.score > float(item.score):
            raise HTTPException(400, f"分数不能超过本项分值 {float(item.score)}")

    row = (
        await db.execute(
            select(Score).where(
                Score.item_id == body.item_id, Score.bidder_id == body.bidder_id, Score.expert_id == user.id
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    old = float(row.score) if row is not None and row.score is not None else None

    if row is None:
        row = Score(
            project_id=project_id,
            item_id=body.item_id,
            bidder_id=body.bidder_id,
            expert_id=user.id,
            score=body.score,
            comment=body.comment.strip(),
            updated_at=now,
        )
        db.add(row)
        await db.flush()
    else:
        row.score = body.score
        row.comment = body.comment.strip()
        row.updated_at = now

    if old != body.score:
        # 改分/首次打分留痕
        action = "score_create" if old is None else "score_change"
        db.add(
            ScoreHistory(
                score_id=row.id,
                item_id=body.item_id,
                bidder_id=body.bidder_id,
                expert_id=user.id,
                old_score=old,
                new_score=body.score,
                reason=body.reason.strip(),
            )
        )
        await audit(
            db,
            action=action,
            user_id=user.id,
            username=user.username,
            target_type="score",
            target_id=row.id,
            detail={"item_id": body.item_id, "bidder_id": body.bidder_id, "old": old, "new": body.score, "reason": body.reason.strip()},
        )
    await db.commit()
    return {"ok": True, "score": body.score, "previous": old}


@router.get("/score/summary")
async def score_summary(
    project_id: int,
    bidder_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """组长/admin 汇总视图：各专家分数与均分（专家本人不可访问）。"""
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
    bidders_q = select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id)
    if bidder_id is not None:
        bidders_q = bidders_q.where(Bidder.id == bidder_id)
    bidders = list((await db.execute(bidders_q)).scalars())

    rows_q = (
        select(Score, User)
        .join(User, User.id == Score.expert_id)
        .where(Score.project_id == project_id)
        .order_by(Score.item_id, Score.bidder_id, User.id)
    )
    if bidder_id is not None:
        rows_q = rows_q.where(Score.bidder_id == bidder_id)
    score_rows = list(await db.execute(rows_q))

    experts_total = (
        await db.execute(
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.member_role.in_(("expert", "leader")))
        )
    ).scalar_one()

    grouped: dict[tuple[int, int], list] = {}
    for s, u in score_rows:
        grouped.setdefault((s.item_id, s.bidder_id), []).append(
            {
                "expert_id": u.id,
                "expert_name": u.display_name or u.username,
                "score": float(s.score) if s.score is not None else None,
                "comment": s.comment,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
        )

    out = []
    for b in bidders:
        item_rows = []
        bidder_total = 0.0
        for it in items:
            entries = grouped.get((it.id, b.id), [])
            vals = [e["score"] for e in entries if e["score"] is not None]
            avg = round(sum(vals) / len(vals), 2) if vals else None
            if avg is not None:
                bidder_total += avg
            item_rows.append(
                {
                    "item": _item_out(it),
                    "scores": entries,
                    "average": avg,
                    "scored_experts": len(vals),
                }
            )
        out.append({"bidder": {"id": b.id, "name": b.name}, "items": item_rows, "total_average": round(bidder_total, 2)})

    return {"bidders": out, "experts_total": experts_total}
