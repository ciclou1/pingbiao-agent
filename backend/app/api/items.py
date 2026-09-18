"""评审条目库路由：抽取任务、列表、编辑确认、手工录入（PRD 3.2 人工在环）。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import get_current_user
from ..database import get_db
from ..models import RequirementItem, User
from ..worker import enqueue
from .deps import require_project, require_project_manager, require_project_member

router = APIRouter(prefix="/projects/{project_id}/requirement-items", tags=["requirement-items"])

ITEM_TYPES = ("qualification", "compliance", "scoring")


class ItemIn(BaseModel):
    item_type: str
    category: str = ""
    clause_text: str
    requirement: str = ""
    score: float | None = None
    source_page: int | None = None


class ItemUpdateIn(BaseModel):
    item_type: str | None = None
    category: str | None = None
    clause_text: str | None = None
    requirement: str | None = None
    score: float | None = None
    source_page: int | None = None
    expert_confirmed: bool | None = None


def _item_out(it: RequirementItem) -> dict:
    return {
        "id": it.id,
        "item_type": it.item_type,
        "category": it.category,
        "clause_text": it.clause_text,
        "requirement": it.requirement,
        "score": float(it.score) if it.score is not None else None,
        "source_page": it.source_page,
        "source_doc_id": it.source_doc_id,
        "ai_extracted": it.ai_extracted,
        "expert_confirmed": it.expert_confirmed,
        "confirmed_by": it.confirmed_by,
        "confirmed_at": it.confirmed_at.isoformat() if it.confirmed_at else None,
        "sort_order": it.sort_order,
    }


@router.post("/extract")
async def start_extract(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    job = await enqueue(db, "extract_tender_items", {"project_id": project_id}, created_by=user.id)
    await audit(
        db,
        action="tender_extract_start",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"job_id": job.id},
    )
    await db.commit()
    return {"job_id": job.id}


@router.get("")
async def list_items(
    project_id: int,
    item_type: str | None = None,
    confirmed: bool | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)

    q = select(RequirementItem).where(RequirementItem.project_id == project_id)
    if item_type:
        if item_type not in ITEM_TYPES:
            raise HTTPException(400, f"item_type 必须是 {'/'.join(ITEM_TYPES)}")
        q = q.where(RequirementItem.item_type == item_type)
    if confirmed is not None:
        q = q.where(RequirementItem.expert_confirmed == confirmed)
    rows = await db.execute(q.order_by(RequirementItem.item_type, RequirementItem.sort_order, RequirementItem.id))
    items = list(rows.scalars())
    return {
        "items": [_item_out(it) for it in items],
        "summary": {
            t: {
                "total": sum(1 for it in items if it.item_type == t),
                "confirmed": sum(1 for it in items if it.item_type == t and it.expert_confirmed),
            }
            for t in ITEM_TYPES
        },
    }


@router.post("")
async def create_item(
    project_id: int,
    body: ItemIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    if body.item_type not in ITEM_TYPES:
        raise HTTPException(400, f"item_type 必须是 {'/'.join(ITEM_TYPES)}")
    if not body.clause_text.strip() and not body.requirement.strip():
        raise HTTPException(400, "条款原文与要求要点至少填一项")
    it = RequirementItem(
        project_id=project_id,
        item_type=body.item_type,
        category=body.category.strip(),
        clause_text=body.clause_text.strip(),
        requirement=body.requirement.strip(),
        score=body.score,
        source_page=body.source_page,
        ai_extracted=False,  # 手工录入
        expert_confirmed=True,  # 手工录入即视为已确认
        confirmed_by=user.id,
        confirmed_at=datetime.now(timezone.utc),
    )
    db.add(it)
    await db.flush()
    await audit(db, action="item_create", user_id=user.id, username=user.username, target_type="requirement_item", target_id=it.id)
    await db.commit()
    return _item_out(it)


@router.patch("/{item_id}")
async def update_item(
    project_id: int,
    item_id: int,
    body: ItemUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    it = await db.get(RequirementItem, item_id)
    if it is None or it.project_id != project_id:
        raise HTTPException(404, "条目不存在")
    if body.item_type is not None:
        if body.item_type not in ITEM_TYPES:
            raise HTTPException(400, f"item_type 必须是 {'/'.join(ITEM_TYPES)}")
        it.item_type = body.item_type
    for field in ("category", "clause_text", "requirement"):
        v = getattr(body, field)
        if v is not None:
            setattr(it, field, v.strip())
    if body.score is not None:
        it.score = body.score
    if body.source_page is not None:
        it.source_page = body.source_page
    if body.expert_confirmed is not None:
        it.expert_confirmed = body.expert_confirmed
        if body.expert_confirmed:
            it.confirmed_by = user.id
            it.confirmed_at = datetime.now(timezone.utc)
        else:
            it.confirmed_by = None
            it.confirmed_at = None
    await audit(
        db,
        action="item_update",
        user_id=user.id,
        username=user.username,
        target_type="requirement_item",
        target_id=it.id,
        detail={"confirmed": it.expert_confirmed},
    )
    await db.commit()
    return _item_out(it)


@router.delete("/{item_id}")
async def delete_item(
    project_id: int,
    item_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    it = await db.get(RequirementItem, item_id)
    if it is None or it.project_id != project_id:
        raise HTTPException(404, "条目不存在")
    await db.delete(it)
    await audit(db, action="item_delete", user_id=user.id, username=user.username, target_type="requirement_item", target_id=item_id)
    await db.commit()
    return {"ok": True}
