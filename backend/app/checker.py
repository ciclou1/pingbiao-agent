"""确定性校验器：对 LLM 判定做规则复核与保守降级（PRD 3.3）。

规则：
  R1 无证据                        → review
  R2 判 failed 但无引用页码         → review（不得凭无据否决）
  R3 判 passed 但置信度 < 0.6       → review
  R4 数值门槛复核（邻近关键词门控）：
     下限语义（至少/不少于/满 N、不足/少于/低于 N）→ 证据数值全部低于门槛 → failed
     上限语义（不超过/不得高于/超过 N，金额类）    → 证据数值超过门槛     → failed
  R5 只做保守降级与补强，绝不把 failed/review 提升为 passed。

校验过程写入 ai_check 便于追溯。
"""
import re

from .parser import join_split_digits

# 下限语义（正面表述）：至少 2 个 / 不少于 90 日
MIN_RE = re.compile(
    r"(?:不少于|不低于|至少|不得少于|须满|满)\s*[^0-9]{0,6}?([\d.]+)\s*(个|项|年|日|天|月|次|万元|元)?"
)
# 下限语义（否定式表述）：不足 90 日 / 少于 2 个 —— 同样要求达到该门槛
NEG_MIN_RE = re.compile(r"(?:不足|少于|低于)\s*[^0-9]{0,6}?([\d.]+)\s*(个|项|年|日|天|月|次)")
# 上限语义：不超过 118 万元 / 不得高于 118 万元 / 超过 118 万元
MAX_RE = re.compile(
    r"(?:不超过|不得高于|不得超过|不得超|超过|高于)\s*[^0-9]{0,8}?([\d.]+)\s*(万元|元)"
)
EVID_NUM_RE = re.compile(r"([\d.]+)\s*(个|项|年|日|天|月|次|万元|元)")

# 数值检查的邻近关键词门控：避免注册资本、供货周期等无关数字被误判
GATES = {
    "业绩": ("业绩", "合同", "项目", "供货"),
    "有效期": ("有效期",),
    "报价": ("报价", "总价", "投标价", "价格", "限价"),
    "保证金": ("保证金", "担保"),
    "财务": ("财务", "审计", "资产", "负债"),
    "注册资本": ("注册资本", "实收资本"),
}
COUNT_UNITS = ("个", "项", "年", "日", "天", "月", "次")
MONEY_UNITS = ("万元", "元")


def _nearby(text: str, idx: int, keywords: tuple[str, ...], window: int = 40) -> bool:
    seg = text[max(0, idx - window) : idx + window]
    return any(k in seg for k in keywords)


def _gate_for(query: str) -> tuple[str, ...] | None:
    for name, kws in GATES.items():
        if name in query:
            return kws
    if any(k in query for k in ("万", "金额", "限价", "控制价")):
        return GATES["报价"]
    return None


def _evidence_numbers(evid_text: str, unit: str, gate: tuple[str, ...] | None) -> list[float]:
    evid_text = join_split_digits(evid_text)
    return [
        _num(m.group(1))
        for m in EVID_NUM_RE.finditer(evid_text)
        if m.group(2) == unit and (gate is None or _nearby(evid_text, m.start(), gate))
    ]


def check(item: dict, verdict: dict, evidence: list[dict]) -> dict:
    """item: {clause_text, requirement, item_type}；verdict: LLM 结论；evidence: 检索证据。

    返回 {'state', 'reason_suffix', 'fired'}。
    """
    state = verdict.get("state") or "review"
    conf = verdict.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else 0.0
    pages = [p for p in (verdict.get("evidence_pages") or []) if isinstance(p, int)]
    fired: list[str] = []
    suffix = ""

    query = f"{item.get('clause_text','')} {item.get('requirement','')}"

    # R1 无证据 → 待复核（绝不自动否决）
    if not evidence:
        return {"state": "review", "reason_suffix": "（未检索到相关材料，转专家复核）", "fired": ["R1_no_evidence"]}

    # R2 判不通过必须引用证据页
    if state == "failed" and not pages:
        fired.append("R2_failed_without_citation")
        state = "review"
        suffix = "（AI 判不通过但未引用具体证据页，转专家复核）"

    # R3 低置信度
    if state == "passed" and conf < 0.6:
        fired.append("R3_low_confidence")
        state = "review"
        suffix = "（置信度不足，转专家复核）"

    evid_text = "\n".join(e.get("text", "") for e in evidence)
    gate = _gate_for(query)

    # R4a 下限语义
    for pattern in (MIN_RE, NEG_MIN_RE):
        hit = False
        for m in pattern.finditer(query):
            val, unit = _num(m.group(1)), (m.group(2) or "")
            if unit not in COUNT_UNITS:
                continue
            found = _evidence_numbers(evid_text, unit, gate)
            if found and max(found) < val:
                fired.append(f"R4_min_{unit}_{val:g}_evidence_max_{max(found):g}")
                if state != "failed":
                    state = "failed"
                    suffix = f"（校验器：材料中{unit}数为 {max(found):g}，低于要求的 {val:g}）"
                hit = True
                break
        if hit:
            break

    # R4b 上限语义（金额类）
    if state != "failed":
        for m in MAX_RE.finditer(query):
            val, unit = _num(m.group(1)), (m.group(2) or "")
            if unit not in MONEY_UNITS:
                continue
            found = _evidence_numbers(evid_text, unit, gate)
            if found and max(found) > val:
                fired.append(f"R4_max_{unit}_{val:g}_evidence_max_{max(found):g}")
                state = "failed"
                suffix = f"（校验器：材料中金额 {max(found):g}{unit} 超过上限 {val:g}{unit}）"
                break

    return {"state": state, "reason_suffix": suffix, "fired": fired}


def _num(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0
