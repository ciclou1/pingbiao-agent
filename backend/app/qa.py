"""专家问答：检索增强 + 强制引文（PRD 3.5）。材料不足时必须回答"未找到"，不编造。"""
import logging

from .llm import chat_json
from .retriever import snippet

log = logging.getLogger("qa")

QA_SYSTEM = (
    "你是评标辅助助手，依据给定的招标/投标材料回答问题。"
    "只能使用材料中的信息，不得推测或补充外部知识；严格按 JSON 输出。"
)

QA_TEMPLATE = """依据下列材料回答问题。

规则：
1. 只能使用材料中的信息，不得推测、不得补充外部知识。
2. 材料不足以回答时，answer 写「材料中未找到相关内容」，insufficient 填 true，不要编造。
3. 引用材料内容时用【第X页】标注页码。
4. 涉及多家投标人的问题，按投标人分点说明，并明确各自的出处页码。

材料：
{evidence}

问题：{question}

严格输出 JSON：
{{"answer": "回答正文", "used_pages": [1, 3], "insufficient": false}}
"""

MAX_EVIDENCE = 10


async def fetch_chunks(db, project_id: int, scope: str = "all", bidder_id: int | None = None) -> list[dict]:
    """按范围取分块（带出处：文件名 / 投标人名 / 页码）。"""
    from sqlalchemy import select

    from .models import Bidder, DocChunk, Document

    q = (
        select(DocChunk, Document, Bidder)
        .join(Document, Document.id == DocChunk.document_id)
        .outerjoin(Bidder, Bidder.id == DocChunk.bidder_id)
        .where(DocChunk.project_id == project_id)
    )
    if scope == "tender":
        q = q.where(DocChunk.doc_role == "tender")
    elif scope == "bidder":
        q = q.where(DocChunk.bidder_id == bidder_id)
    rows = await db.execute(q)
    out = []
    for chunk, doc, bidder in rows:
        out.append(
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "page_no": chunk.page_no,
                "text": chunk.text,
                "doc_name": doc.original_name,
                "doc_role": chunk.doc_role,
                "bidder_id": chunk.bidder_id,
                "bidder_name": bidder.name if bidder else "",
            }
        )
    return out


def _evidence_block(evidence: list[dict]) -> str:
    lines = []
    for e in evidence:
        source = e.get("bidder_name") or "招标文件"
        lines.append(f"【来源：{source} · {e.get('doc_name','')} 第{e.get('page')}页】\n{snippet(e.get('text',''), 600)}")
    return "\n\n".join(lines)


def _citations(evidence: list[dict], used_pages: list) -> list[dict]:
    cited = {p for p in used_pages if isinstance(p, int)}
    return [
        {
            "doc_id": e.get("document_id"),
            "doc_name": e.get("doc_name"),
            "bidder_id": e.get("bidder_id"),
            "bidder_name": e.get("bidder_name"),
            "page": e.get("page"),
            "snippet": snippet(e.get("text", ""), 300),
            "cited": e.get("page") in cited,
        }
        for e in evidence
    ]


async def answer_question(retrieve_fn, question: str, has_material: bool = True) -> dict:
    """检索 + LLM 作答。返回 {answer, citations, insufficient, model}。

    retrieve_fn(query, top_k) 由调用方注入（关键词 / 向量混合检索），
    检索实现与问答逻辑解耦，便于后续替换检索策略。
    """
    if not has_material:
        return {
            "answer": "材料中未找到相关内容（该项目尚未上传可检索的文件，或文件解析未完成）。",
            "citations": [],
            "insufficient": True,
            "model": "",
        }

    evidence = await retrieve_fn(question, MAX_EVIDENCE)
    if not evidence:
        return {
            "answer": "材料中未找到相关内容（未检索到与问题相关的段落，可换一种问法或缩小到具体投标人）。",
            "citations": [],
            "insufficient": True,
            "model": "",
        }

    try:
        data = await chat_json(
            None,
            purpose="qa_answer",
            system=QA_SYSTEM,
            user=QA_TEMPLATE.format(evidence=_evidence_block(evidence), question=question),
            temperature=0.1,
            max_tokens=3000,
        )
    except Exception as exc:
        log.warning("qa llm failed: %s", exc)
        return {
            "answer": f"AI 作答失败（{type(exc).__name__}），请重试或改用原文定位查看。",
            "citations": _citations(evidence, []),
            "insufficient": False,
            "model": "",
        }

    from .config import settings

    return {
        "answer": str(data.get("answer") or "").strip() or "材料中未找到相关内容。",
        "citations": _citations(evidence, data.get("used_pages") or []),
        "insufficient": bool(data.get("insufficient")),
        "model": settings.llm_model,
    }
