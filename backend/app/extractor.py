"""招标文件评审条目抽取：关键词定位 → 分片 → 分片并发 LLM 抽取 → 合并去重（PRD 3.2/5.4）。"""
import asyncio
import re
from difflib import SequenceMatcher

from .llm import chat_json
from .parser import Page

ITEM_TYPES = ("qualification", "compliance", "scoring")
TYPE_LABELS = {"qualification": "资格条件", "compliance": "符合性(否决)", "scoring": "评分项"}

# 评审相关页定位关键词（命中页 ±1 邻页纳入，覆盖跨页表格）
HOT_KEYWORDS = (
    "资格", "资质", "业绩要求", "财务", "项目负责人",
    "符合性", "否决", "废标", "无效投标",
    "评分办法", "评分细则", "评分标准", "评分因素", "评审因素", "分值",
    "评标办法", "投标保证金", "最高限价", "控制价",
)

SYSTEM_PROMPT = "你是资深的招标评标专家，负责从招标文件中抽取评审条目。严格按 JSON 输出，不要输出任何其他内容。"

USER_PROMPT = """从以下招标文件文本（带【第X页】页码标记）中抽取评审条目，分三类：

1. qualification（资格条件）：投标人必须具备的资格要求（营业执照、资质证书、类似业绩、财务状况、人员资格、体系认证等）。
2. compliance（符合性审查/否决项）：不满足即否决投标/废标的条款（未按要求密封签字盖章、未缴保证金、报价超最高限价、有效期不足、失信名单、格式不全等）。
3. scoring（评分项）：综合评分法中的打分项及子项，含分值与评分标准原文。

字段要求：
- clause_text：尽量摘录条款原文（不要改写、不要概括）
- requirement：一句话概括要求要点（可量化的量化）
- category：分类（资质/业绩/财务/人员/体系认证/保证金/密封签章/报价/有效期/信用/工期/质保/方案/其他）
- score：评分项填数字分值，资格/符合性条目填 null
- source_page：条款所在页码（依据【第X页】标记，跨页取起始页；无法判断填 null）

注意：只抽评审相关条款；招标公告、联系方式、目录等无关内容不抽。某类没有就返回空数组，宁缺毋滥。

严格输出 JSON：
{"items": [{"item_type": "qualification", "category": "资质", "clause_text": "...", "requirement": "...", "score": null, "source_page": 1}]}

招标文件文本：
"""

CHUNK_BUDGET = 3200  # 每片字符预算（中文），配合页码标记


def locate_hot_pages(pages: list[Page], max_pages: int = 60) -> list[Page]:
    """关键词定位评审相关页 ±1 邻页；无命中时回退取前 30 页。"""
    hits = {p.page_no for p in pages if any(kw in (p.text or "") for kw in HOT_KEYWORDS)}
    if not hits:
        return [p for p in pages if p.page_no <= 30]
    extended: set[int] = set()
    for n in hits:
        extended.update((n - 1, n, n + 1))
    return [p for p in pages if p.page_no in extended][:max_pages]


def build_chunks(pages: list[Page], budget: int = CHUNK_BUDGET) -> list[str]:
    """按页拼接分片，每片带页码标记，控制字符预算。"""
    chunks: list[str] = []
    buf: list[str] = []
    used = 0
    for p in pages:
        text = (p.text or "").strip()
        if not text:
            continue
        marked = f"【第{p.page_no}页】\n{text}"
        if buf and used + len(marked) > budget:
            chunks.append("\n\n".join(buf))
            buf, used = [], 0
        buf.append(marked)
        used += len(marked)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def dedupe_items(items: list[dict], existing_texts: list[str]) -> tuple[list[dict], int]:
    """片间去重 + 与库中已有条目去重。

    判定同条：前 200 字归一化后相似度 > 0.8，**或**前 60 字完全相同
    （同一条款被两次抽取时尾部措辞常有差异，光看相似度会漏掉）。
    """
    seen: list[str] = [_norm(t)[:200] for t in existing_texts]
    seen_heads: set[str] = {s[:60] for s in seen if s}
    fresh: list[dict] = []
    skipped = 0
    for it in items:
        key = _norm(str(it.get("clause_text") or it.get("requirement") or ""))[:200]
        if not key:
            skipped += 1
            continue
        head = key[:60]
        if head in seen_heads or any(key == s or SequenceMatcher(None, key, s).ratio() > 0.8 for s in seen):
            skipped += 1
            continue
        seen.append(key)
        seen_heads.add(head)
        fresh.append(it)
    return fresh, skipped


def _coerce_item(it: dict) -> dict | None:
    it["item_type"] = str(it.get("item_type") or "").strip().lower()
    if it["item_type"] not in ITEM_TYPES:
        return None
    score = it.get("score")
    if score in ("", "null", None):
        it["score"] = None
    else:
        try:
            it["score"] = round(float(score), 2)
        except (TypeError, ValueError):
            it["score"] = None
    try:
        it["source_page"] = int(it.get("source_page"))
    except (TypeError, ValueError):
        it["source_page"] = None
    it["category"] = str(it.get("category") or "").strip()
    it["clause_text"] = str(it.get("clause_text") or "").strip()
    it["requirement"] = str(it.get("requirement") or "").strip()
    if not it["clause_text"] and not it["requirement"]:
        return None
    return it


async def extract_from_pages(db, pages: list[Page], progress_cb=None, concurrency: int = 3) -> tuple[list[dict], int]:
    """主流程：定位 → 分片 → 分片并发抽取 → 合并去重。返回（条目列表, 分片数）。

    分片之间互不依赖，且上游单次调用可能很慢（实测 5~77 秒），
    并发跑可把 15 个分片的墙钟时间压到 1/3 左右。
    """
    chunks = build_chunks(locate_hot_pages(pages))
    if not chunks:
        return [], 0

    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[dict] = []
    done = 0
    lock = asyncio.Lock()
    total = len(chunks)

    async def _one(chunk: str) -> None:
        nonlocal done
        async with sem:
            data = await chat_json(
                db, purpose="tender_extract", system=SYSTEM_PROMPT, user=USER_PROMPT + chunk, max_tokens=12000
            )
        raw_items = data.get("items") if isinstance(data, dict) else None
        parsed = []
        for raw in raw_items or []:
            if isinstance(raw, dict):
                it = _coerce_item(raw)
                if it is not None:
                    parsed.append(it)
        async with lock:
            results.extend(parsed)
            done += 1
            if progress_cb:
                await progress_cb(done, total)

    await asyncio.gather(*(_one(c) for c in chunks))
    fresh, _skipped = dedupe_items(results, [])
    return fresh, total
