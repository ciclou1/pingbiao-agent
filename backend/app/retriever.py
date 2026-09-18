"""证据检索：中文无分词场景下的字符 n-gram TF-IDF + 数值加权。

pgvector 向量检索在 M3 接入（需本地 BGE-M3，网关无 embedding 模型）；
本模块接口保持可替换，M3 只需在 score 中叠加向量分。
"""
import math
import re

CJK_RUN = re.compile(r"[一-鿿]+")
NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|元|个|项|年|日|天|月|次|%)?")
ASCII_RE = re.compile(r"[A-Za-z]{2,}\d*")

# 无区分度的高频词：出现于几乎所有分块，直接剔除（IDF 也会压低，但省算力）
STOP_TERMS = {
    "投标", "标人", "标文件", "文件", "本项目", "项目", "招标", "标段", "包件",
    "我方", "贵方", "公司", "以上", "如下", "以及", "按照", "根据", "应当",
}

NGRAM_SIZES = (2, 3, 4)


def terms_of(text: str) -> set[str]:
    """抽取查询词：中文 2-4 gram + 数值（带单位）+ 英文/型号 token。"""
    terms: set[str] = set()
    for run in CJK_RUN.findall(text or ""):
        for n in NGRAM_SIZES:
            for i in range(len(run) - n + 1):
                terms.add(run[i : i + n])
    for m in NUM_RE.finditer(text or ""):
        terms.add(m.group().replace(" ", ""))
    for m in ASCII_RE.finditer(text or ""):
        terms.add(m.group().lower())
    return {t for t in terms if t not in STOP_TERMS}


def numbers_of(text: str) -> set[str]:
    return {m.group().replace(" ", "") for m in NUM_RE.finditer(text or "")}


def build_index(chunks: list) -> dict:
    """为一批分块预建 n-gram 倒排所需数据。

    chunks: [{id, document_id, page_no, text}]
    返回 {chunks, grams: [set, ...], df: {term: doc_freq}, n}
    """
    grams = [terms_of(c["text"]) for c in chunks]
    df: dict[str, int] = {}
    for g in grams:
        for t in g:
            df[t] = df.get(t, 0) + 1
    return {"chunks": chunks, "grams": grams, "df": df, "n": max(1, len(chunks))}


def retrieve(index: dict, query: str, top_k: int = 6, min_score: float = 0.5) -> list[dict]:
    """按 TF-IDF 打分取 top_k 证据分块。query 一般用条目的 条款原文 + 要求要点。"""
    if not index["chunks"]:
        return []
    q_terms = terms_of(query)
    if not q_terms:
        return []
    q_numbers = numbers_of(query)
    n = index["n"]

    scored: list[tuple[float, int]] = []
    for i, g in enumerate(index["grams"]):
        inter = q_terms & g
        if not inter:
            continue
        s = 0.0
        for t in inter:
            # idf：越稀有的词权重越高；长词更具体，略加权
            idf = math.log(n / (1 + index["df"].get(t, 1)))
            s += idf * (1.0 + 0.15 * (len(t) - 2))
        text = index["chunks"][i]["text"]
        for num in q_numbers:
            if num and num in text:
                s += 2.0
        if s >= min_score:
            scored.append((s, i))

    scored.sort(reverse=True)
    out = []
    for s, i in scored[:top_k]:
        c = index["chunks"][i]
        item = {
            "chunk_id": c.get("id"),
            "document_id": c.get("document_id"),
            "page": c.get("page_no"),
            "text": c["text"],
            "score": round(s, 3),
        }
        # 引文卡片需要的出处信息（检索时按需带上）
        for k in ("doc_name", "bidder_name", "bidder_id", "doc_role"):
            if k in c:
                item[k] = c[k]
        out.append(item)
    return out


def snippet(text: str, limit: int = 400) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


# ---------------------------------------------------------------------------
# 混合检索：关键词排名 + 向量排名 → RRF 融合
# ---------------------------------------------------------------------------

_VECTOR_SQL = """
SELECT c.id AS chunk_id, c.document_id, c.page_no, c.text,
       d.original_name AS doc_name, c.bidder_id, b.name AS bidder_name, c.doc_role,
       1 - (c.embedding <=> CAST(:qvec AS vector)) AS sim
FROM doc_chunks c
JOIN documents d ON d.id = c.document_id
LEFT JOIN bidders b ON b.id = c.bidder_id
WHERE c.project_id = :pid AND c.embedding IS NOT NULL
  {extra}
ORDER BY c.embedding <=> CAST(:qvec AS vector)
LIMIT :lim
"""


def _rrf_fuse(rank_lists: list[list[dict]], k: int = 60, weights: list[float] | None = None) -> dict[str, float]:
    """带权倒数排名融合：每个候选累加 w/(k+rank)。

    好处是不需要把两边的分数归一到同一量纲（关键词 TF-IDF 与余弦相似度不可比）。

    权重按**实测**设定（real1 项目 95 条条目 / 8 条口语化提问）：
      - 审查路径：查询就是条款原文，字面重合是最强信号 → 关键词权重高、向量低
        （实测：等权会从 100% 退化到 95.8%，加权后回到 100%）
      - 问答路径：自然语言提问，向量是主力 → 等权
        （实测：关键词 50% → 混合 88%）
    """
    weights = weights or [1.0] * len(rank_lists)
    scores: dict[str, float] = {}
    for w, lst in zip(weights, rank_lists):
        for rank, item in enumerate(lst):
            cid = str(item.get("chunk_id"))
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank + 1)
    return scores


async def hybrid_retrieve(
    db,
    index: dict,
    query: str,
    *,
    project_id: int,
    scope: str = "all",
    bidder_id: int | None = None,
    top_k: int = 6,
    expand_fn=None,
    keyword_weight: float = 1.0,
    vector_weight: float = 1.0,
    vector_mode: str = "fuse",
) -> list[dict]:
    """关键词 + 向量检索。

    两种组合方式，按**实测**分路径选用：
      - `fuse`（默认）：RRF 融合。问答路径用这个——提问是自然语言，
        向量是主力（实测 recall@10：关键词 50% → 混合 88%）。
      - `fallback`：关键词优先、向量只补位（关键词命中不被打掉，仅在证据不足时补齐）。
        审查路径用这个——查询就是条款原文，字面重合是最强信号；
        实测等权融合会把证据定位自洽率从 100% 拉到 95.8%，加权也只是 97.9%。

    - 关键词榜单：查询文本走同义词扩展（靠字面匹配，需要扩展）
    - 向量榜单：pgvector 余弦，查询用**原始问题**（向量天然处理同义改写）
    - 向量服务不可用时，两种模式都自动退化为纯关键词
    """
    from sqlalchemy import text

    from . import embedding
    from .config import settings

    keyword_query = expand_fn(query) if expand_fn else query
    keyword_hits = retrieve(index, keyword_query, top_k=settings.vector_candidate_limit)
    lists = [keyword_hits]
    vector_hits: list[dict] = []

    if embedding.is_enabled():
        vecs = await __import__("asyncio").to_thread(embedding.embed_texts, [query])
        if vecs:
            extra = []
            params = {
                "pid": project_id,
                "qvec": embedding.to_pgvector(vecs[0]),
                "lim": settings.vector_candidate_limit,
            }
            if scope == "bidder" and bidder_id is not None:
                extra.append("AND c.bidder_id = :bid")
                params["bid"] = bidder_id
            elif scope == "tender":
                extra.append("AND c.doc_role = 'tender'")
            sql = _VECTOR_SQL.format(extra=" ".join(extra))
            try:
                rows = await db.execute(text(sql), params)
                vector_hits = [
                    {
                        "chunk_id": r.chunk_id,
                        "document_id": r.document_id,
                        "page": r.page_no,
                        "text": r.text,
                        "doc_name": r.doc_name,
                        "bidder_id": r.bidder_id,
                        "bidder_name": r.bidder_name or "",
                        "doc_role": r.doc_role,
                        "score": round(float(r.sim), 4),
                        "source": "vector",
                    }
                    for r in rows
                ]
                if vector_hits and vector_mode == "fuse":
                    lists.append(vector_hits)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger("retriever").warning("向量检索失败，本次仅用关键词", exc_info=True)

    if vector_mode == "fallback":
        # 关键词优先：原样保留，仅当证据条数不足时用向量补齐（不重复、不打乱已有顺序）
        out = list(keyword_hits[:top_k])
        if len(out) < top_k and vector_hits:
            seen = {str(h.get("chunk_id")) for h in out}
            for h in vector_hits:
                if len(out) >= top_k:
                    break
                if str(h.get("chunk_id")) not in seen:
                    out.append(h)
                    seen.add(str(h.get("chunk_id")))
        return out

    fused = _rrf_fuse(lists, k=settings.rrf_k, weights=[keyword_weight] + [vector_weight] * (len(lists) - 1))
    if not fused:
        return []

    # 用最先出现的元数据（关键词榜单优先）组结果
    by_id: dict[str, dict] = {}
    for lst in lists:
        for item in lst:
            by_id.setdefault(str(item.get("chunk_id")), item)

    ordered = sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]
    out = []
    for cid, score in ordered:
        item = dict(by_id.get(cid) or {})
        if not item:
            continue
        item["rrf"] = round(score, 5)
        item.setdefault("score", round(score, 5))
        out.append(item)
    return out
