"""分块向量化：解析入库后建索引，以及存量数据的批量补向量。

向量是增强项：embed 服务不可用时只记警告并返回统计，不抛错，
检索侧会自动退回关键词模式（见 retriever.hybrid_retrieve）。
"""
import asyncio
import logging
import time

from sqlalchemy import text

from . import embedding

log = logging.getLogger("indexer")

UPDATE_SQL = text(
    "UPDATE doc_chunks SET embedding = CAST(:vec AS vector), embedded_at = now() WHERE id = :cid"
)

# embedding 列由 migrations 用原生 DDL 添加，ORM 模型里不声明（保持模型与
# pgvector 解耦：扩展不可用时启动不会失败），故此处也用原生 SQL 读写。
SELECT_PENDING_SQL = "SELECT id, text FROM doc_chunks WHERE embedding IS NULL {extra} ORDER BY id LIMIT :lim"


async def embed_chunks(
    db,
    *,
    project_id: int | None = None,
    document_id: int | None = None,
    limit: int = 5000,
) -> dict:
    """给尚未向量化的分块补向量。返回 {pending, embedded, skipped}。"""
    if not embedding.is_enabled():
        return {"pending": 0, "embedded": 0, "skipped": "向量服务未配置"}

    extra = []
    params: dict = {"lim": limit}
    if project_id is not None:
        extra.append("AND project_id = :pid")
        params["pid"] = project_id
    if document_id is not None:
        extra.append("AND document_id = :did")
        params["did"] = document_id

    rows = (await db.execute(text(SELECT_PENDING_SQL.format(extra=" ".join(extra))), params)).all()
    if not rows:
        return {"pending": 0, "embedded": 0, "skipped": ""}

    ids = [r[0] for r in rows]
    texts = [r[1] or "" for r in rows]
    t0 = time.monotonic()
    vecs = await asyncio.to_thread(embedding.embed_texts, texts)
    embed_sec = time.monotonic() - t0
    if vecs is None:
        log.warning("向量服务不可用，%s 个分块本次未建索引", len(ids))
        return {"pending": len(ids), "embedded": 0, "skipped": "向量服务不可用"}

    t1 = time.monotonic()
    for cid, vec in zip(ids, vecs):
        await db.execute(UPDATE_SQL, {"vec": embedding.to_pgvector(vec), "cid": cid})
    await db.commit()
    write_sec = time.monotonic() - t1
    log.info(
        "已向量化 %s 个分块：编码 %.1fs（%.0fms/条），写库 %.1fs（%.1fms/条）",
        len(ids),
        embed_sec,
        embed_sec / len(ids) * 1000,
        write_sec,
        write_sec / len(ids) * 1000,
    )
    return {
        "pending": len(ids),
        "embedded": len(ids),
        "skipped": "",
        "embed_seconds": round(embed_sec, 2),
        "write_seconds": round(write_sec, 2),
    }


async def coverage(db, project_id: int | None = None) -> dict:
    """向量覆盖率（用于自检与体检报告）。"""
    sql = (
        "SELECT count(*) AS total, count(embedding) AS embedded FROM doc_chunks"
        + (" WHERE project_id = :pid" if project_id is not None else "")
    )
    row = (await db.execute(text(sql), {"pid": project_id} if project_id is not None else {})).one()
    total, embedded = int(row.total or 0), int(row.embedded or 0)
    return {"total": total, "embedded": embedded, "ratio": round(embedded / total, 3) if total else None}
