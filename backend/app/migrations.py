"""幂等 DDL 迁移。

新增表由 SQLAlchemy create_all 负责；**已有表的字段、索引、扩展**在此维护
（create_all 不会给已存在的表加列）。

所有语句都写成 IF NOT EXISTS，可重复执行；pgvector 不可用时只记警告，
检索自动退回关键词模式，不影响主流程。
"""
import logging

from sqlalchemy import text

from .database import engine

log = logging.getLogger("migrate")

EMBED_DIM = 1024  # BGE-M3 dense 向量维度

STATEMENTS: list[tuple[str, str]] = [
    ("pgvector 扩展", "CREATE EXTENSION IF NOT EXISTS vector"),
    ("doc_chunks.embedding", f"ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM})"),
    (
        "doc_chunks 向量索引（HNSW 余弦）",
        "CREATE INDEX IF NOT EXISTS ix_doc_chunks_embedding ON doc_chunks "
        "USING hnsw (embedding vector_cosine_ops)",
    ),
    ("doc_chunks.embedded_at", "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS embedded_at timestamptz"),
    (
        "doc_chunks 待嵌入索引",
        "CREATE INDEX IF NOT EXISTS ix_doc_chunks_pending_embed ON doc_chunks (id) WHERE embedding IS NULL",
    ),
]


async def run_migrations() -> dict:
    """返回 {名称: 是否成功}，便于启动日志与自检脚本查看。"""
    result: dict[str, bool] = {}
    for name, sql in STATEMENTS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            result[name] = True
        except Exception as exc:  # noqa: BLE001
            log.warning("迁移跳过（%s）：%s", name, str(exc)[:200])
            result[name] = False
    return result


async def vector_ready() -> bool:
    """pgvector 扩展与向量列是否就绪（服务自检/检索降级判断用）。"""
    try:
        async with engine.begin() as conn:
            row = await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='doc_chunks' AND column_name='embedding'"
                )
            )
            return (row.scalar_one() or 0) > 0
    except Exception:  # noqa: BLE001
        return False
