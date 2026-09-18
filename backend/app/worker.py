"""后台任务 worker：DB 任务表轮询消费 + 注册式处理器。

M0：parse_document（解析+分块）；M1：extract_tender_items（条目抽取）；
M2：review_bidder（条目 × 投标人 → 三态结论 / 评分建议）。
"""
import asyncio
import datetime as dt
import logging
import traceback
from pathlib import Path

from sqlalchemy import delete, func, select, update

from .config import settings
from .database import SessionLocal
from .models import (
    Bidder,
    DocChunk,
    Document,
    Job,
    PriceItem,
    Project,
    RequirementItem,
    ReviewResult,
    ScoreSuggestion,
)
from .parser import chunk_pages, extract_pages, guess_page_count

log = logging.getLogger("worker")

# 页均字符低于此值视为无文本层（扫描件/图片型 PDF）
MIN_CHARS_PER_PAGE = 20

HANDLERS: dict[str, callable] = {}


def handler(job_type: str):
    def _register(fn):
        HANDLERS[job_type] = fn
        return fn

    return _register


async def enqueue(db, job_type: str, payload: dict, created_by: int | None = None) -> Job:
    job = Job(job_type=job_type, payload=payload, created_by=created_by)
    db.add(job)
    await db.flush()
    return job


def _read_sync(path: str) -> bytes:
    return Path(path).read_bytes()


async def _run_one(job_id: int) -> None:
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status != "pending":
            return
        job.status = "running"
        job.started_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
        fn = HANDLERS.get(job.job_type)
        try:
            if fn is None:
                raise RuntimeError(f"未注册的任务类型: {job.job_type}")
            result = await fn(db, job)
            job.status = "succeeded"
            job.progress = 100
            job.result = result or {}
        except Exception:
            job.status = "failed"
            job.error = traceback.format_exc()[-4000:]
            log.exception("job %s failed", job_id)
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()


async def recover_stale_jobs() -> int:
    """进程重启后把卡在 running 的任务重排（各任务为幂等 upsert，可安全重跑）。"""
    async with SessionLocal() as db:
        res = await db.execute(
            update(Job).where(Job.status == "running").values(status="pending", progress=0, message="服务重启后重排")
        )
        await db.commit()
        return res.rowcount or 0


async def worker_loop(poll_seconds: float = 1.0) -> None:
    """多 worker 竞争：FOR UPDATE SKIP LOCKED 保证一条任务只被一个 worker 取走。"""
    recovered = await recover_stale_jobs()
    if recovered:
        log.warning("已将 %s 个中断任务重排为待执行", recovered)
    while True:
        try:
            async with SessionLocal() as db:
                rows = await db.execute(
                    select(Job.id)
                    .where(Job.status == "pending")
                    .order_by(Job.id)
                    .limit(settings.worker_concurrency)
                    .with_for_update(skip_locked=True)
                )
                ids = [r[0] for r in rows]
            if ids:
                await asyncio.gather(*(_run_one(i) for i in ids))
                continue
        except Exception:
            log.exception("worker loop error")
        await asyncio.sleep(poll_seconds)


# ---------------------------------------------------------------------------
# 解析 + 分块
# ---------------------------------------------------------------------------

@handler("parse_document")
async def parse_document(db, job: Job) -> dict:
    doc_id = job.payload["document_id"]
    doc = await db.get(Document, doc_id)
    if doc is None:
        raise RuntimeError(f"document {doc_id} 不存在")
    doc.parse_status = "parsing"
    await db.commit()

    raw = await asyncio.to_thread(_read_sync, doc.stored_path)
    pages = await asyncio.to_thread(extract_pages, raw, doc.original_name)
    doc.page_count = len(pages) or guess_page_count(raw, doc.original_name)

    # 分块入库（证据检索用），重解析时先清旧块
    await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
    pieces = chunk_pages(pages)
    for i, (page_no, text) in enumerate(pieces):
        db.add(
            DocChunk(
                document_id=doc.id,
                project_id=doc.project_id,
                bidder_id=doc.bidder_id,
                doc_role=doc.doc_role,
                page_no=page_no,
                chunk_no=i,
                text=text,
            )
        )
    # 扫描件识别：有页但每页几乎没有可提取字符 → 无文本层，须走 OCR 或人工
    total_chars = sum(len(p.text or "") for p in pages)
    avg_chars = total_chars / len(pages) if pages else 0
    note = ""
    if pages and avg_chars < MIN_CHARS_PER_PAGE:
        from .ocr import fill_pages, is_enabled

        if is_enabled():
            added = await asyncio.to_thread(fill_pages, pages, raw, doc.original_name)
            total_chars = sum(len(p.text or "") for p in pages)
            avg_chars = total_chars / len(pages)
            if added:
                # OCR 补回了文本：重新分块，走正常检索管线
                await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
                pieces = chunk_pages(pages)
                for i, (page_no, text) in enumerate(pieces):
                    db.add(
                        DocChunk(
                            document_id=doc.id,
                            project_id=doc.project_id,
                            bidder_id=doc.bidder_id,
                            doc_role=doc.doc_role,
                            page_no=page_no,
                            chunk_no=i,
                            text=text,
                        )
                    )
                note = f"扫描件，OCR 补回 {added} 字"
        if avg_chars < MIN_CHARS_PER_PAGE:
            doc.parse_status = "manual_required"
            note = note or f"每页平均仅 {avg_chars:.0f} 个字符，疑似扫描件（未启用 OCR），需人工处理"
        else:
            doc.parse_status = "parsed"
    else:
        doc.parse_status = "parsed" if pages else "manual_required"
        if not pages:
            note = "未提取到任何文本（格式不支持或容器已加密），需人工处理"

    # 报价清单：额外解析成明细行，供清标使用
    price_rows = 0
    if doc.doc_role == "price" and doc.bidder_id and doc.original_name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        from .clearing import parse_price_sheet

        await db.execute(delete(PriceItem).where(PriceItem.document_id == doc.id))
        rows = await asyncio.to_thread(parse_price_sheet, raw)
        for r in rows:
            db.add(
                PriceItem(
                    project_id=doc.project_id,
                    bidder_id=doc.bidder_id,
                    document_id=doc.id,
                    **r,
                )
            )
        price_rows = len(rows)

    await db.commit()

    # 向量索引：分块入库后补向量（服务不可用时自动跳过，检索退化为关键词）
    embed_stat = {}
    if pieces:
        from .indexer import embed_chunks

        embed_stat = await embed_chunks(db, document_id=doc.id)

    return {
        "pages": len(pages),
        "chunks": len(pieces),
        "chars": total_chars,
        "price_rows": price_rows,
        "embedded": embed_stat.get("embedded", 0),
        "note": note,
    }


@handler("embed_chunks")
async def embed_chunks_job(db, job: Job) -> dict:
    """存量分块补向量（支持按项目批量）。"""
    from .indexer import coverage, embed_chunks

    project_id = job.payload.get("project_id")
    total_embedded = 0
    rounds = 0
    while rounds < 200:
        rounds += 1
        stat = await embed_chunks(db, project_id=project_id, limit=500)
        total_embedded += stat.get("embedded", 0)
        if stat.get("embedded", 0) < 500:
            break
        job.message = f"已向量化 {total_embedded} 个分块"
        job.progress = min(95, rounds * 5)
        await db.commit()
    cov = await coverage(db, project_id)
    return {"embedded": total_embedded, "coverage": cov}


# ---------------------------------------------------------------------------
# 招标文件 → 评审条目库
# ---------------------------------------------------------------------------

@handler("extract_tender_items")
async def extract_tender_items(db, job: Job) -> dict:
    from .extractor import TYPE_LABELS, extract_from_pages

    project_id = job.payload["project_id"]
    rows = await db.execute(
        select(Document)
        .where(Document.project_id == project_id, Document.doc_role == "tender")
        .order_by(Document.id.desc())
    )
    tender_docs = list(rows.scalars())
    doc = next((d for d in tender_docs if d.parse_status == "parsed"), None)
    if doc is None:
        raise RuntimeError("没有解析成功的招标文件，请先上传并等待解析完成")

    # 分片并发抽取 → 进度回写必须串行化（AsyncSession 不支持并发提交）
    progress_lock = asyncio.Lock()

    async def _progress(done: int, total: int) -> None:
        pct = int(done / total * 80)
        if pct == job.progress:
            return
        async with progress_lock:
            try:
                job.progress = pct
                job.message = f"LLM 抽取分片 {done}/{total}"
                await db.commit()
            except Exception:  # noqa: BLE001
                log.warning("进度回写失败（不影响抽取）", exc_info=True)
                await db.rollback()

    raw = await asyncio.to_thread(_read_sync, doc.stored_path)
    pages = await asyncio.to_thread(extract_pages, raw, doc.original_name)
    if not pages:
        doc.parse_status = "manual_required"
        await db.commit()
        raise RuntimeError("招标文件无法提取文本（可能是扫描件），暂不支持自动抽取，需人工录入条目")

    items, chunk_count = await extract_from_pages(db, pages, progress_cb=_progress)

    # 与库中已有条目去重：重新抽取（或分片重叠）不应产生重复条目
    from .extractor import dedupe_items

    existing = list(
        (
            await db.execute(select(RequirementItem.clause_text).where(RequirementItem.project_id == project_id))
        ).scalars()
    )
    fresh, dup_skipped = dedupe_items(items, existing)

    had_existing = (
        await db.execute(
            select(func.count()).select_from(RequirementItem).where(RequirementItem.project_id == project_id)
        )
    ).scalar_one() > 0

    order_base = 0
    if had_existing:
        max_order = await db.execute(
            select(func.max(RequirementItem.sort_order)).where(RequirementItem.project_id == project_id)
        )
        order_base = (max_order.scalar_one() or 0) + 1

    added = {"qualification": 0, "compliance": 0, "scoring": 0}
    for i, it in enumerate(fresh):
        db.add(
            RequirementItem(
                project_id=project_id,
                item_type=it["item_type"],
                category=it["category"],
                clause_text=it["clause_text"],
                requirement=it["requirement"],
                score=it["score"],
                source_page=it["source_page"],
                source_doc_id=doc.id,
                ai_extracted=True,
                sort_order=order_base + i,
            )
        )
        added[it["item_type"]] += 1
    await db.commit()
    return {
        "chunks": chunk_count,
        "items_total": sum(added.values()),
        "by_type": {TYPE_LABELS[k]: v for k, v in added.items()},
        "duplicates_skipped": dup_skipped,
        "appended": had_existing,
    }


# ---------------------------------------------------------------------------
# 条目 × 投标人 → 三态结论 / 评分建议（M2 核心）
# ---------------------------------------------------------------------------

def _item_dict(it: RequirementItem, project_params: str = "") -> dict:
    requirement = it.requirement or ""
    # 招标文件常把关键门槛值留空或写在前附表（真实实测：示范文本的投标有效期、
    # 预算/最高限价都是空的），只靠文本判不了 → 把项目级参数补进「要求要点」，
    # 并标注来源，避免与招标文件原文混淆。
    clause = it.clause_text or ""
    if project_params and any(k in clause for k in ("报价", "限价", "预算", "投标有效期")):
        requirement = (requirement + project_params).strip()
    return {
        "id": it.id,
        "item_type": it.item_type,
        "category": it.category,
        "clause_text": it.clause_text,
        "requirement": requirement,
        "score": float(it.score) if it.score is not None else None,
    }


@handler("review_bidder")
async def review_bidder(db, job: Job) -> dict:
    from .retriever import build_index
    from .reviewer import review_bidder_items

    project_id = job.payload["project_id"]
    bidder_id = job.payload["bidder_id"]

    bidder = await db.get(Bidder, bidder_id)
    if bidder is None or bidder.project_id != project_id:
        raise RuntimeError("投标人不存在或不属于该项目")

    # 项目级评审参数（控制价以万元录入），供条目补充门槛值
    project = await db.get(Project, project_id)
    param_parts = []
    if project is not None and project.control_price is not None:
        param_parts.append(f"本项目最高限价/预算为 {float(project.control_price):g} 万元")
    project_params = ("（补充参数：" + "；".join(param_parts) + "）") if param_parts else ""

    items = list(
        (
            await db.execute(
                select(RequirementItem)
                .where(
                    RequirementItem.project_id == project_id,
                    RequirementItem.expert_confirmed.is_(True),
                )
                .order_by(RequirementItem.item_type, RequirementItem.sort_order, RequirementItem.id)
            )
        ).scalars()
    )
    if not items:
        raise RuntimeError("该项目还没有已确认的评审条目，请先在条目库确认")

    chunk_rows = list(
        (
            await db.execute(
                select(DocChunk).where(DocChunk.project_id == project_id, DocChunk.bidder_id == bidder_id)
            )
        ).scalars()
    )
    if not chunk_rows:
        log.warning("bidder %s 没有可检索材料（未上传/未解析），全部条目将判待复核", bidder_id)

    # 证据要标出「来自哪份文件」：一个投标人可能传多份（商务标/技术标/标书包）
    doc_names = {
        d.id: d.original_name
        for d in (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars()
    }

    index = await asyncio.to_thread(
        build_index,
        [
            {
                "id": c.id,
                "document_id": c.document_id,
                "doc_name": doc_names.get(c.document_id, ""),
                "page_no": c.page_no,
                "text": c.text,
            }
            for c in chunk_rows
        ],
    )

    # 进度回写：条目是并发审查的，必须串行化 commit（AsyncSession 不支持并发提交），
    # 且进度写失败绝不能影响审查本身。
    progress_lock = asyncio.Lock()

    async def _progress(done: int, total: int) -> None:
        pct = int(done / total * 95)
        if pct == job.progress:
            return
        async with progress_lock:
            try:
                job.progress = pct
                job.message = f"审查中 {done}/{total}"
                await db.commit()
            except Exception:  # noqa: BLE001
                log.warning("进度回写失败（不影响审查）", exc_info=True)
                await db.rollback()

    item_dicts = [_item_dict(it, project_params) for it in items]

    # 检索注入：关键词 + 向量
    # 审查路径用 fallback 模式（关键词优先、向量只补位）：查询就是条款原文，
    # 字面重合是最强信号——实测等权融合会把证据定位自洽率从 100% 拉到 95.8%。
    from .retriever import hybrid_retrieve
    from .synonyms import expand

    async def retrieve_fn(query: str, k: int) -> list[dict]:
        return await hybrid_retrieve(
            db,
            index,
            query,
            project_id=project_id,
            scope="bidder",
            bidder_id=bidder_id,
            top_k=k,
            expand_fn=expand,
            vector_mode="fallback",
        )

    out = await review_bidder_items(item_dicts, retrieve_fn, progress_cb=_progress)

    now = dt.datetime.now(dt.timezone.utc)
    existing_reviews = {
        r.item_id: r
        for r in (
            await db.execute(
                select(ReviewResult).where(
                    ReviewResult.project_id == project_id, ReviewResult.bidder_id == bidder_id
                )
            )
        ).scalars()
    }
    existing_suggestions = {
        s.item_id: s
        for s in (
            await db.execute(
                select(ScoreSuggestion).where(
                    ScoreSuggestion.project_id == project_id, ScoreSuggestion.bidder_id == bidder_id
                )
            )
        ).scalars()
    }

    counts = {"passed": 0, "failed": 0, "review": 0}
    suggested = 0
    for it in items:
        if it.item_type == "scoring":
            s = out["suggestions"].get(it.id)
            if s is None:
                continue
            row = existing_suggestions.get(it.id)
            if row is None:
                row = ScoreSuggestion(project_id=project_id, item_id=it.id, bidder_id=bidder_id)
                db.add(row)
            row.summary = s["summary"]
            row.suggested_score = s["suggested_score"]
            row.range_low = s["range_low"]
            row.range_high = s["range_high"]
            row.evidence = s["evidence"]
            row.confidence = s["confidence"]
            row.model = settings.llm_model
            row.updated_at = now
            suggested += 1
        else:
            r = out["reviews"].get(it.id)
            if r is None:
                continue
            row = existing_reviews.get(it.id)
            if row is None:
                row = ReviewResult(project_id=project_id, item_id=it.id, bidder_id=bidder_id)
                db.add(row)
            # AI 字段刷新；专家终值保留（人工结论不被覆盖）
            row.ai_state = r["state"]
            row.ai_reason = r["reason"]
            row.ai_evidence = r["evidence"]
            row.ai_confidence = r["confidence"]
            row.ai_check = r["check"]
            row.ai_model = settings.llm_model
            row.updated_at = now
            counts[r["state"]] = counts.get(r["state"], 0) + 1

    await db.commit()
    return {
        "bidder": bidder.name,
        "reviewed": sum(counts.values()),
        "by_state": counts,
        "score_suggestions": suggested,
        "chunks": len(chunk_rows),
    }


# ---------------------------------------------------------------------------
# 独立进程入口：docker compose 的 worker 服务执行 `python -m app.worker`
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("worker 启动，并发上限 %s", settings.worker_concurrency)
    await worker_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("worker 退出")
