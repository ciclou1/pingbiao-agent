"""清标与围标串标预警路由（PRD 3.6）。

两类检查都是纯计算（解析/相似度/规则），不调 LLM，因此同步执行不等模型。
所有结论只是供专家参考的异常提示，不进入否决结论。
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import get_current_user
from ..clearing import run_clearing_checks
from ..collusion import file_metadata, run_collusion_checks
from ..database import get_db
from ..models import Alert, Bidder, DocChunk, Document, PriceItem, User
from .deps import require_project, require_project_manager, require_project_member

router = APIRouter(prefix="/projects/{project_id}", tags=["clearing"])

LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}


def _alert_out(a: Alert) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "code": a.code,
        "level": a.level,
        "title": a.title,
        "detail": a.detail or {},
        "bidders": a.bidders or [],
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def _price_items_by_bidder(db: AsyncSession, project_id: int) -> dict[int, list[dict]]:
    """返回按投标人分组的报价明细。数值保持 Decimal（清标走精确计算），JSON 输出时再转 float。"""
    rows = await db.execute(select(PriceItem).where(PriceItem.project_id == project_id).order_by(PriceItem.id))
    out: dict[int, list[dict]] = {}
    for it in rows.scalars():
        out.setdefault(it.bidder_id, []).append(
            {
                "id": it.id,
                "sheet_name": it.sheet_name,
                "row_no": it.row_no,
                "item_name": it.item_name,
                "unit": it.unit,
                "qty": it.qty,
                "unit_price": it.unit_price,
                "amount": it.amount,
            }
        )
    return out


def _rows_json(rows: list[dict]) -> list[dict]:
    def f(v):
        return float(v) if v is not None else None

    return [{**r, "qty": f(r["qty"]), "unit_price": f(r["unit_price"]), "amount": f(r["amount"])} for r in rows]


@router.get("/clearing/items")
async def clearing_items(
    project_id: int,
    bidder_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    by_bidder = await _price_items_by_bidder(db, project_id)
    if bidder_id is not None:
        return {"items": _rows_json(by_bidder.get(bidder_id, [])), "bidder_id": bidder_id}
    bidders = {b.id: b.name for b in (await db.execute(select(Bidder).where(Bidder.project_id == project_id))).scalars()}
    return {
        "by_bidder": [
            {"bidder_id": bid, "bidder_name": bidders.get(bid, ""), "count": len(rows), "items": _rows_json(rows)}
            for bid, rows in sorted(by_bidder.items())
        ]
    }


@router.post("/clearing/run")
async def run_clearing(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await require_project(db, project_id)
    await require_project_manager(db, project_id, user)

    bidders = list((await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))).scalars())
    by_bidder = await _price_items_by_bidder(db, project_id)
    if not by_bidder:
        raise HTTPException(400, "尚未导入报价清单，请先上传投标人的报价表（Excel）并等待解析完成")

    alerts = await asyncio.to_thread(
        run_clearing_checks,
        {"control_price": float(project.control_price) if project.control_price is not None else None},
        [{"id": b.id, "name": b.name} for b in bidders],
        by_bidder,
    )

    await db.execute(delete(Alert).where(Alert.project_id == project_id, Alert.kind == "clearing"))
    for a in alerts:
        db.add(Alert(project_id=project_id, kind="clearing", **a))
    await audit(
        db,
        action="clearing_run",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"alerts": len(alerts), "bidders_with_sheet": len(by_bidder)},
    )
    await db.commit()
    return {"alerts": len(alerts), "bidders_with_sheet": len(by_bidder)}


@router.post("/collusion/run")
async def run_collusion(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)

    bidders = list((await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))).scalars())
    chunks = list(
        (
            await db.execute(
                select(DocChunk).where(DocChunk.project_id == project_id, DocChunk.bidder_id.is_not(None))
            )
        ).scalars()
    )
    if not chunks:
        raise HTTPException(400, "尚未上传/解析投标文件，无法做文件层分析")

    texts: dict[int, str] = {}
    for c in chunks:
        texts[c.bidder_id] = texts.get(c.bidder_id, "") + c.text

    docs = list(
        (
            await db.execute(
                select(Document).where(
                    Document.project_id == project_id, Document.bidder_id.is_not(None), Document.parse_status == "parsed"
                )
            )
        ).scalars()
    )
    meta_by_bidder: dict[int, list[dict]] = {}

    def _read_meta(doc):
        try:
            raw = Path(doc.stored_path).read_bytes()
        except OSError:
            return doc.bidder_id, None
        m = file_metadata(raw, doc.original_name)
        m["file"] = doc.original_name
        return doc.bidder_id, m

    results = await asyncio.gather(*(asyncio.to_thread(_read_meta, d) for d in docs))
    for bid, m in results:
        if m:
            meta_by_bidder.setdefault(bid, []).append(m)

    price_items = await _price_items_by_bidder(db, project_id)
    alerts = await asyncio.to_thread(
        run_collusion_checks,
        [{"id": b.id, "name": b.name} for b in bidders],
        texts,
        meta_by_bidder,
        price_items,
    )

    await db.execute(delete(Alert).where(Alert.project_id == project_id, Alert.kind == "collusion"))
    for a in alerts:
        db.add(Alert(project_id=project_id, kind="collusion", **a))
    await audit(
        db,
        action="collusion_run",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"alerts": len(alerts)},
    )
    await db.commit()
    return {"alerts": len(alerts)}


@router.get("/alerts")
async def list_alerts(
    project_id: int,
    kind: str | None = Query(None, description="clearing / collusion"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_member(db, project_id, user)
    q = select(Alert).where(Alert.project_id == project_id)
    if kind:
        if kind not in ("clearing", "collusion"):
            raise HTTPException(400, "kind 必须是 clearing/collusion")
        q = q.where(Alert.kind == kind)
    rows = list((await db.execute(q)).scalars())
    rows.sort(key=lambda a: (LEVEL_ORDER.get(a.level, 9), -a.id))
    return {"alerts": [_alert_out(a) for a in rows]}
