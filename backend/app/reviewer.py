"""评审判定：条目 × 投标人 → 三态结论 / 评分建议（LLM + 确定性校验）。"""
import asyncio
import logging

from .checker import check as rule_check
from .llm import chat_json
from .retriever import snippet

log = logging.getLogger("reviewer")

REVIEW_SYSTEM = (
    "你是资深评标专家，依据招标文件的评审条目，对投标人提交的材料作出判断。"
    "只依据给定材料，不得臆测；严格按 JSON 输出，不要输出其他内容。"
)

REVIEW_TEMPLATE = """【评审条目】
类型：{type_label}
条款原文：{clause}
要求要点：{requirement}

【投标人材料摘录】（来自投标文件，带【第X页】页码标记）
{evidence}

判定规则：
- 材料充分支持满足要求 → state="passed"
- 材料明确显示不满足（缺少必需文件、数值低于门槛、条件不符等）→ state="failed"
- 材料不足以判断、内容含糊、需核验原件 → state="review"

注意：仅"未检索到某项内容"不得判 failed，应判 review；证据只能引用上面摘录内容。

严格输出 JSON：
{{"state": "passed|failed|review", "confidence": 0.0, "reason": "一句话判定理由", "evidence_pages": [1]}}
"""

SCORE_SYSTEM = (
    "你是资深评标专家，依据评分细则对投标人材料给出评分建议。"
    "只依据给定材料，不得臆测；严格按 JSON 输出，不要输出其他内容。"
)

SCORE_TEMPLATE = """【评分项】
条款原文：{clause}
要求要点：{requirement}
本项分值：{max_score}

【投标人材料摘录】（带【第X页】页码标记）
{evidence}

请给出评分建议：
- summary：该投标人在本评分项的响应要点（2-3 句，必须基于材料；材料缺失时说明缺什么）
- suggested_score：建议分（0 到 {max_score} 之间；材料不足时填 null）
- range_low / range_high：合理区间（可为 null）
- confidence：0-1
- evidence_pages：依据页码

严格输出 JSON：
{{"summary": "...", "suggested_score": null, "range_low": null, "range_high": null, "confidence": 0.0, "evidence_pages": [1]}}
"""

TYPE_LABELS = {"qualification": "资格条件", "compliance": "符合性审查(否决项)", "scoring": "评分项"}
MAX_EVIDENCE_CHUNKS = 6


def evidence_block(evidence: list[dict]) -> str:
    if not evidence:
        return "（未检索到相关材料）"
    lines = []
    for e in evidence:
        lines.append(f"【第{e.get('page')}页】{snippet(e.get('text', ''), 500)}")
    return "\n\n".join(lines)


async def judge_item(item: dict, evidence: list[dict]) -> dict:
    """LLM 三态判定 + 确定性校验，返回 {state, reason, evidence, confidence, check}。"""
    query = f"{item.get('clause_text','')} {item.get('requirement','')}"
    payload = {
        "type_label": TYPE_LABELS.get(item.get("item_type"), item.get("item_type")),
        "clause": item.get("clause_text", ""),
        "requirement": item.get("requirement", "") or "（见条款原文）",
        "evidence": evidence_block(evidence),
    }
    try:
        data = await chat_json(
            None,
            purpose="review_judge",
            system=REVIEW_SYSTEM,
            user=REVIEW_TEMPLATE.format(**payload),
            max_tokens=2000,
        )
    except Exception as exc:  # LLM 全链路失败 → 待复核，不阻塞评审
        log.warning("judge llm failed: %s", exc)
        return {
            "state": "review",
            "reason": f"AI 判定调用失败，转专家复核（{type(exc).__name__}）",
            "evidence": _pack_evidence(evidence, []),
            "confidence": None,
            "check": {"fired": ["llm_error"], "reason_suffix": ""},
            "model": "",
        }

    state = str(data.get("state") or "review").strip().lower()
    if state not in ("passed", "failed", "review"):
        state = "review"
    verdict = {
        "state": state,
        "confidence": data.get("confidence"),
        "reason": str(data.get("reason") or "").strip(),
        "evidence_pages": data.get("evidence_pages") or [],
    }
    result = rule_check(item, verdict, evidence)
    check = {"fired": result["fired"], "reason_suffix": result["reason_suffix"]}
    reason = verdict["reason"] + (result["reason_suffix"] or "")
    return {
        "state": result["state"],
        "reason": reason,
        "evidence": _pack_evidence(evidence, verdict["evidence_pages"]),
        "confidence": verdict["confidence"] if isinstance(verdict["confidence"], (int, float)) else None,
        "check": check,
        "model": "",
    }


async def suggest_score(item: dict, evidence: list[dict]) -> dict:
    """评分项建议：要点摘要 + 参考区间（只建议，不落最终分）。"""
    max_score = item.get("score")
    payload = {
        "clause": item.get("clause_text", ""),
        "requirement": item.get("requirement", "") or "（见条款原文）",
        "max_score": max_score if max_score is not None else "（未标明，按条款原文判断）",
        "evidence": evidence_block(evidence),
    }
    try:
        data = await chat_json(
            None, purpose="score_suggest", system=SCORE_SYSTEM, user=SCORE_TEMPLATE.format(**payload), max_tokens=2500
        )
    except Exception as exc:
        log.warning("score suggest failed: %s", exc)
        return {"summary": f"AI 建议生成失败（{type(exc).__name__}），请专家独立打分", "suggested_score": None,
                "range_low": None, "range_high": None, "confidence": None, "evidence": _pack_evidence(evidence, [])}

    def _f(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    suggested = _f(data.get("suggested_score"))
    if suggested is not None and max_score is not None:
        suggested = max(0.0, min(suggested, float(max_score)))
    return {
        "summary": str(data.get("summary") or "").strip(),
        "suggested_score": suggested,
        "range_low": _f(data.get("range_low")),
        "range_high": _f(data.get("range_high")),
        "confidence": data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
        "evidence": _pack_evidence(evidence, data.get("evidence_pages") or []),
    }


def _pack_evidence(evidence: list[dict], pages: list) -> list[dict]:
    """打包证据：AI 引用过的页优先，其余按检索分排序，最多 6 条。"""
    cited = set(p for p in pages if isinstance(p, int))
    ordered = sorted(evidence, key=lambda e: (e.get("page") not in cited, -float(e.get("score") or 0)))
    return [
        {
            "doc_id": e.get("document_id"),
            "doc_name": e.get("doc_name", ""),
            "page": e.get("page"),
            "snippet": snippet(e.get("text", ""), 400),
            "score": e.get("score"),
            "cited": e.get("page") in cited,
        }
        for e in ordered[:MAX_EVIDENCE_CHUNKS]
    ]


async def review_bidder_items(items: list[dict], retrieve_fn, concurrency: int = 3, progress_cb=None) -> dict:
    """并发处理一个投标人的全部条目。返回 {'reviews': {...}, 'suggestions': {...}}。

    retrieve_fn(query, top_k) 由调用方注入（关键词 / 向量混合检索），
    本模块不关心检索实现；也不触碰 DB 会话：LLM 并发安全，落库由调用方串行完成。
    """
    sem = asyncio.Semaphore(concurrency)
    reviews: dict[int, dict] = {}
    suggestions: dict[int, dict] = {}
    done = 0
    total = len(items)

    async def _one(item: dict):
        nonlocal done
        async with sem:
            query = f"{item.get('clause_text','')} {item.get('requirement','')}"
            evidence = await retrieve_fn(query, MAX_EVIDENCE_CHUNKS)
            if item.get("item_type") == "scoring":
                out = await suggest_score(item, evidence)
                suggestions[item["id"]] = out
            else:
                out = await judge_item(item, evidence)
                reviews[item["id"]] = out
            done += 1
            if progress_cb:
                await progress_cb(done, total)

    await asyncio.gather(*(_one(it) for it in items))
    return {"reviews": reviews, "suggestions": suggestions}
