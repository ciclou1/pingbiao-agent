"""清标：报价清单解析 + 算术核对 / 超限价 / 不平衡报价 / 漏项（PRD 3.6）。

所有结论只是供专家参考的异常提示，不进入否决结论。
"""
import re
from decimal import Decimal, InvalidOperation

HEADER_KEYS = {
    "item_name": ("项目名称", "货物名称", "材料名称", "清单名称", "品名", "名称", "项目"),
    "unit": ("计量单位", "单位"),
    "qty": ("数量", "工程量"),
    "unit_price": ("综合单价", "材料单价", "单价"),
    "amount": ("合价", "金额", "合计", "总价", "复价"),
}
TOTAL_NAME_RE = re.compile(r"^(合计|总计|总价|合\s*计|投标总价)")


def _num(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "").replace("￥", "").replace("¥", "").replace(" ", "")
    if not s or s in ("-", "—", "/"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _map_header(row: list) -> dict | None:
    """识别表头行 → {字段: 列号}；命中不足 3 个字段视为不是表头。"""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        text = str(cell or "").strip()
        if not text:
            continue
        for field, keys in HEADER_KEYS.items():
            if field in mapping:
                continue
            if any(k == text or k in text for k in keys):
                mapping[field] = idx
                break
    return mapping if len(mapping) >= 3 else None


def parse_price_sheet(raw: bytes) -> list[dict]:
    """解析报价清单 xlsx → 明细行。"""
    from openpyxl import load_workbook
    import io

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    out: list[dict] = []
    for ws in wb.worksheets:
        header = None
        header_row = 0
        for i, row in enumerate(ws.iter_rows(values_only=True, max_row=40), start=1):
            mapping = _map_header(list(row))
            if mapping:
                header, header_row = mapping, i
                break
        if header is None:
            continue
        for i, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            cells = list(row)

            def get(field):
                idx = header.get(field)
                return cells[idx] if idx is not None and idx < len(cells) else None

            name = str(get("item_name") or "").strip()
            # 合计行常把「合计」写在序号列，名称列为空 —— 整行扫一遍关键字，
            # 否则合计会被当成普通明细行，导致总额翻倍。
            if not name or not TOTAL_NAME_RE.match(name):
                for cell in cells:
                    s = str(cell or "").strip()
                    if s and TOTAL_NAME_RE.match(s):
                        name = s
                        break
            if not name and _num(get("amount")) is None:
                continue
            out.append(
                {
                    "sheet_name": ws.title,
                    "row_no": i,
                    "item_name": name[:300],
                    "unit": str(get("unit") or "").strip()[:32],
                    "qty": _num(get("qty")),
                    "unit_price": _num(get("unit_price")),
                    "amount": _num(get("amount")),
                }
            )
    return out


def _norm_name(name: str) -> str:
    return re.sub(r"[\s　()（）【】\[\].,，、:：;；\-—_/]", "", (name or "").lower())


def run_clearing_checks(project, bidders: list, items_by_bidder: dict[int, list[dict]]) -> list[dict]:
    """返回预警列表 [{code, level, title, detail, bidders}]。"""
    alerts: list[dict] = []
    name_of = {b["id"]: b["name"] for b in bidders}

    # 1) 算术核对
    for bid, rows in items_by_bidder.items():
        bad = []
        for r in rows:
            qty, up, amt = r["qty"], r["unit_price"], r["amount"]
            if qty is None or up is None or amt is None:
                continue
            expect = qty * up
            tol = max(Decimal("0.01"), abs(amt) * Decimal("0.001"))
            if abs(expect - amt) > tol:
                bad.append(
                    {
                        "row_no": r["row_no"],
                        "item_name": r["item_name"],
                        "qty": float(qty),
                        "unit_price": float(up),
                        "amount": float(amt),
                        "expected": float(round(expect, 2)),
                    }
                )
        if bad:
            alerts.append(
                {
                    "code": "arithmetic",
                    "level": "high",
                    "title": f"{name_of[bid]} 报价表算术错误 {len(bad)} 处（数量×单价≠合价）",
                    "detail": {"rows": bad[:20], "count": len(bad)},
                    "bidders": [{"id": bid, "name": name_of[bid]}],
                }
            )

    # 2) 超最高限价（项目控制价按万元录入，报价表一般按元；口径差 100 倍时按万元解读该表）
    if project.get("control_price") is not None:
        limit_yuan = Decimal(str(project["control_price"])) * 10000
        for bid, rows in items_by_bidder.items():
            total = _total_of(rows)
            if total is None:
                continue
            scale = Decimal(1)
            if total > 0 and total < limit_yuan / 100:
                scale = Decimal(10000)  # 该表疑似以万元为单位
            effective = total * scale
            if effective > limit_yuan:
                alerts.append(
                    {
                        "code": "over_limit",
                        "level": "high",
                        "title": f"{name_of[bid]} 报价总额 {float(effective):,.2f} 元 超过控制价 {float(limit_yuan):,.2f} 元",
                        "detail": {
                            "total": float(effective),
                            "limit": float(limit_yuan),
                            "sheet_total": float(total),
                            "assumed_unit": "万元" if scale != 1 else "元",
                        },
                        "bidders": [{"id": bid, "name": name_of[bid]}],
                    }
                )

    # 3) 不平衡报价：同一条目在各家间的单价中位数偏离
    by_name: dict[str, dict[int, dict]] = {}
    for bid, rows in items_by_bidder.items():
        for r in rows:
            key = _norm_name(r["item_name"])
            if not key or r["unit_price"] is None:
                continue
            by_name.setdefault(key, {})[bid] = r
    unbalanced = []
    for key, per_bid in by_name.items():
        if len(per_bid) < 2:
            continue
        prices = sorted(float(r["unit_price"]) for r in per_bid.values())
        mid = prices[len(prices) // 2]
        if mid <= 0:
            continue
        for bid, r in per_bid.items():
            p = float(r["unit_price"])
            if p > mid * 1.5:
                unbalanced.append(
                    {
                        "row_no": r["row_no"],
                        "item_name": r["item_name"],
                        "bidder": name_of[bid],
                        "bidder_id": bid,
                        "unit_price": p,
                        "median": mid,
                        "qty": float(r["qty"]) if r["qty"] is not None else None,
                    }
                )
    if unbalanced:
        unbalanced.sort(key=lambda x: -(x["qty"] or 0))
        alerts.append(
            {
                "code": "unbalanced",
                "level": "medium",
                "title": f"疑似不平衡报价 {len(unbalanced)} 处（单价显著高于各家同项中位数）",
                "detail": {"rows": unbalanced[:20], "count": len(unbalanced)},
                "bidders": [{"id": b["id"], "name": b["name"]} for b in bidders],
            }
        )

    # 4) 漏项：多数投标人有、个别没有的条目
    if len(items_by_bidder) >= 2:
        appears = {}
        for key, per_bid in by_name.items():
            appears[key] = set(per_bid.keys())
        display = {}
        for rows in items_by_bidder.values():
            for r in rows:
                display.setdefault(_norm_name(r["item_name"]), r["item_name"])
        missing = []
        all_bids = set(items_by_bidder.keys())
        for key, present in appears.items():
            if len(present) >= 2 and present != all_bids:
                for bid in all_bids - present:
                    missing.append(
                        {
                            "item_name": display.get(key, key),
                            "bidder": name_of[bid],
                            "bidder_id": bid,
                            "present_in": [name_of[b] for b in present],
                        }
                    )
        if missing:
            alerts.append(
                {
                    "code": "missing_item",
                    "level": "medium",
                    "title": f"疑似漏项 {len(missing)} 处（多数投标人报了该条目，个别未报）",
                    "detail": {"rows": missing[:20], "count": len(missing)},
                    "bidders": [{"id": b["id"], "name": b["name"]} for b in bidders],
                }
            )
    return alerts


def _total_of(rows: list[dict]) -> Decimal | None:
    """优先取「合计/总价」行，否则汇总各行合价。"""
    for r in rows:
        if TOTAL_NAME_RE.match(r["item_name"] or "") and r["amount"] is not None:
            return r["amount"]
    total = Decimal("0")
    found = False
    for r in rows:
        if r["amount"] is not None and not TOTAL_NAME_RE.match(r["item_name"] or ""):
            total += r["amount"]
            found = True
    return total if found else None
