"""围标串标预警：文件元数据 / 文本相似度 / 报价规律（PRD 3.6）。

只用投标文件自身的特征（不依赖交易平台数据）；结论仅供专家参考，不进入否决结论。
"""
import datetime as dt
import hashlib
import re
import zipfile
from decimal import Decimal

# 相似度阈值：字符 3-gram simhash 的汉明距离
HAMMING_HIGH = 10  # ≤10 → 高度雷同
HAMMING_MEDIUM = 18  # ≤18 → 需关注
# 文本太短时 simhash 噪声大，不做相似度比较
MIN_SIMILARITY_CHARS = 2000
META_TIME_WINDOW_MIN = 30  # 生成时间相差在此窗口内视为同时段生成

# 办公软件/生成工具/系统默认值：出现在「作者」里不代表串标（真实场景几乎人人如此）
GENERIC_AUTHOR_HINTS = (
    "openpyxl", "python-docx", "pymupdf", "fitz", "aspose", "spire", "itext", "reportlab",
    "wps", "kingsoft", "microsoft", "office", "libreoffice", "openoffice", "apache",
    "acrobat", "foxit", "nitro", "pdf", "wkhtmltopdf", "chrome", "headless",
    "word", "excel", "powerpoint", "windows", "administrator", "admin", "user", "system",
    "default", "unknown", "微软", "用户", "管理员", "扫描", "打印机", "scanner", "电脑",
)


def is_generic_author(value: str) -> bool:
    low = (value or "").lower().strip()
    if len(low) < 2:
        return True
    return any(h in low for h in GENERIC_AUTHOR_HINTS)


def _hash64(text: str) -> int:
    return int.from_bytes(hashlib.md5(text.encode("utf-8", "ignore")).digest()[:8], "big")


def simhash64(text: str, limit: int = 300_000) -> int:
    """字符 3-gram simhash（中文分词无关）。"""
    t = re.sub(r"\s+", "", (text or "")[:limit])
    if len(t) < 3:
        return 0
    v = [0] * 64
    for i in range(len(t) - 2):
        h = _hash64(t[i : i + 3])
        for b in range(64):
            v[b] += 1 if (h >> b) & 1 else -1
    out = 0
    for b in range(64):
        if v[b] > 0:
            out |= 1 << b
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def similarity(a: int, b: int) -> float:
    return round(1 - hamming(a, b) / 64, 3)


# ---------------------------------------------------------------------------
# 文件元数据
# ---------------------------------------------------------------------------

def _parse_pdf_date(s: str) -> dt.datetime | None:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", s or "")
    if not m:
        return None
    try:
        return dt.datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def _parse_iso(s: str) -> dt.datetime | None:
    s = (s or "").strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
        return d.replace(tzinfo=None)
    except ValueError:
        return None


def file_metadata(raw: bytes, filename: str) -> dict:
    """返回 {author, last_modified_by, creator, producer, app, created, modified}（缺失为空）。"""
    name = (filename or "").lower()
    meta = {"author": "", "last_modified_by": "", "creator": "", "producer": "", "app": "", "created": None, "modified": None}
    try:
        if name.endswith(".pdf"):
            import fitz

            with fitz.open(stream=raw, filetype="pdf") as doc:
                md = doc.metadata or {}
            meta["author"] = (md.get("author") or "").strip()
            meta["creator"] = (md.get("creator") or "").strip()
            meta["producer"] = (md.get("producer") or "").strip()
            meta["created"] = _parse_pdf_date(md.get("creationDate", ""))
            meta["modified"] = _parse_pdf_date(md.get("modDate", ""))
        elif name.endswith((".docx", ".xlsx", ".pptx")):
            with zipfile.ZipFile(__import__("io").BytesIO(raw)) as zf:
                core = zf.read("docProps/core.xml").decode("utf-8", "ignore") if "docProps/core.xml" in zf.namelist() else ""
                app = zf.read("docProps/app.xml").decode("utf-8", "ignore") if "docProps/app.xml" in zf.namelist() else ""

                def tag(xml, t):
                    m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", xml, re.S)
                    return (m.group(1).strip() if m else "")

                meta["author"] = tag(core, "dc:creator")
                meta["last_modified_by"] = tag(core, "cp:lastModifiedBy")
                meta["created"] = _parse_iso(tag(core, "dcterms:created"))
                meta["modified"] = _parse_iso(tag(core, "dcterms:modified"))
                meta["app"] = tag(app, "Application")
    except Exception:
        pass
    return meta


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------

def run_collusion_checks(
    bidders: list,
    texts_by_bidder: dict[int, str],
    meta_by_bidder: dict[int, list[dict]],
    price_items: dict[int, list[dict]],
) -> list[dict]:
    alerts: list[dict] = []
    name_of = {b["id"]: b["name"] for b in bidders}

    # 1) 文本相似度（过短文本跳过，阈值内标注文本量以便判断）
    hashes = {bid: simhash64(t) for bid, t in texts_by_bidder.items() if t and len(t) >= MIN_SIMILARITY_CHARS}
    ids = sorted(hashes)
    pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            d = hamming(hashes[a], hashes[b])
            if d <= HAMMING_MEDIUM:
                pairs.append(
                    {
                        "bidders": [name_of[a], name_of[b]],
                        "hamming": d,
                        "similarity": similarity(hashes[a], hashes[b]),
                        "chars": [len(texts_by_bidder[a]), len(texts_by_bidder[b])],
                        "level": "high" if d <= HAMMING_HIGH else "medium",
                    }
                )
    for p in sorted(pairs, key=lambda x: x["hamming"]):
        alerts.append(
            {
                "code": "similarity",
                "level": p["level"],
                "title": f"投标文件文本高度相似：{p['bidders'][0]} ↔ {p['bidders'][1]}（相似度 {p['similarity']:.0%}）",
                "detail": p,
                "bidders": [{"id": b["id"], "name": b["name"]} for b in bidders if b["name"] in p["bidders"]],
            }
        )

    # 2) 元数据：作者类字段（强信号，排除软件默认值）与软件类字段（弱信号）分级
    for field, label in (("author", "文档作者"), ("last_modified_by", "最后保存者")):
        groups: dict[str, list[int]] = {}
        for bid, metas in meta_by_bidder.items():
            for m in metas:
                v = (m.get(field) or "").strip()
                if v and not v.isdigit() and not is_generic_author(v):
                    groups.setdefault(v, []).append(bid)
        for value, bids in groups.items():
            uniq = sorted(set(bids))
            if len(uniq) >= 2:
                alerts.append(
                    {
                        "code": "meta",
                        "level": "high",
                        "title": f"不同投标人的{label}相同：「{value}」（{len(uniq)} 家）",
                        "detail": {"field": field, "value": value, "bidders": [name_of[b] for b in uniq]},
                        "bidders": [{"id": b, "name": name_of[b]} for b in uniq],
                    }
                )

    # 软件/生成器一致：绝大多数标书都由同一办公软件生成，单独不构成串标证据 → 弱信号
    for field, label in (("app", "生成软件"), ("producer", "PDF 生成器"), ("creator", "PDF 创建程序")):
        groups: dict[str, list[int]] = {}
        for bid, metas in meta_by_bidder.items():
            for m in metas:
                v = (m.get(field) or "").strip()
                if v:
                    groups.setdefault(v, []).append(bid)
        for value, bids in groups.items():
            uniq = sorted(set(bids))
            if len(uniq) >= 2:
                alerts.append(
                    {
                        "code": "meta",
                        "level": "low",
                        "title": f"{label}一致：「{value}」（{len(uniq)} 家，弱信号，需结合其他特征）",
                        "detail": {"field": field, "value": value, "bidders": [name_of[b] for b in uniq]},
                        "bidders": [{"id": b, "name": name_of[b]} for b in uniq],
                    }
                )

    # 文件生成/修改时间聚集（相邻时间比较），合并为一条预警
    times: list[tuple[dt.datetime, int]] = []
    for bid, metas in meta_by_bidder.items():
        for m in metas:
            for key in ("created", "modified"):
                if m.get(key):
                    times.append((m[key], bid))
    times.sort()
    near: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for i in range(len(times)):
        t1, b1 = times[i]
        for j in range(i + 1, len(times)):
            t2, b2 = times[j]
            if (t2 - t1).total_seconds() / 60 > META_TIME_WINDOW_MIN:
                break
            if b1 == b2 or (min(b1, b2), max(b1, b2)) in seen:
                continue
            seen.add((min(b1, b2), max(b1, b2)))
            near.append(
                {
                    "bidders": [name_of[b1], name_of[b2]],
                    "gap_minutes": round((t2 - t1).total_seconds() / 60, 1),
                    "time_a": t1.isoformat(sep=" "),
                    "time_b": t2.isoformat(sep=" "),
                }
            )
    if near:
        near.sort(key=lambda x: x["gap_minutes"])
        alerts.append(
            {
                "code": "meta",
                "level": "medium",
                "title": f"文件生成时间聚集：{len(near)} 对投标文件在 {META_TIME_WINDOW_MIN} 分钟内生成",
                "detail": {"rows": near[:20], "count": len(near)},
                "bidders": [{"id": b["id"], "name": b["name"]} for b in bidders],
            }
        )

    # 3) 报价规律：同一条目出现完全相同单价（≥2 家） / 总价等差
    by_name: dict[str, dict[int, Decimal]] = {}
    for bid, rows in price_items.items():
        for r in rows:
            if r.get("unit_price") is None or not r.get("item_name"):
                continue
            if re.match(r"^(合计|总计|总价|投标总价)", r["item_name"].strip()):
                continue  # 合计行不参与单价比较
            key = re.sub(r"[\s　]", "", r["item_name"])
            by_name.setdefault(key, {})[bid] = Decimal(str(r["unit_price"]))

    identical: list[dict] = []
    for key, per_bid in by_name.items():
        if len(per_bid) < 2:
            continue
        price_to_bids: dict[Decimal, list[int]] = {}
        for bid, price in per_bid.items():
            price_to_bids.setdefault(price, []).append(bid)
        for price, bids in price_to_bids.items():
            if len(bids) >= 2:
                identical.append(
                    {
                        "item_name": key,
                        "unit_price": float(price),
                        "bidders": [name_of[b] for b in sorted(bids)],
                        "others_differ": len(per_bid) - len(bids),
                    }
                )
    if identical:
        identical.sort(key=lambda x: (-len(x["bidders"]), x["item_name"]))
        max_share = max(len(x["bidders"]) for x in identical)
        alerts.append(
            {
                "code": "price_pattern",
                "level": "high" if max_share >= 3 else "medium",
                "title": f"不同投标人对 {len(identical)} 个条目报出完全相同的单价（最多 {max_share} 家）",
                "detail": {"rows": identical[:20], "count": len(identical)},
                "bidders": [{"id": b["id"], "name": b["name"]} for b in bidders],
            }
        )

    totals = {bid: _total(rows) for bid, rows in price_items.items()}
    totals = {k: v for k, v in totals.items() if v and v > 0}
    if len(totals) >= 2:
        vals = sorted(totals.items(), key=lambda x: x[1])
        diffs = [vals[i + 1][1] - vals[i][1] for i in range(len(vals) - 1)]
        if len(diffs) >= 2 and len(set(diffs)) == 1:
            alerts.append(
                {
                    "code": "price_pattern",
                    "level": "medium",
                    "title": f"各家报价总额呈等差分布（公差 {float(diffs[0]):,.2f}）",
                    "detail": {"totals": [(name_of[b], float(v)) for b, v in vals]},
                    "bidders": [{"id": b, "name": name_of[b]} for b, _ in vals],
                }
            )
    return alerts


def _total(rows: list[dict]) -> Decimal | None:
    t = Decimal("0")
    found = False
    for r in rows:
        if r.get("amount") is not None:
            t += Decimal(str(r["amount"]))
            found = True
    return t if found else None
