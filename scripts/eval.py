"""评测 runner：跑一个评测样例，量化 抽取召回 / 判定命中 / 引文准确率。

用法：python scripts/eval.py [样例名=demo] [--keep]
  样例目录：eval/cases/<样例名>/（含 case.json 与标书文件）
  报告输出：eval/results/<样例名>-<时间戳>.md / .json
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:5180/api/v1"
ROOT = Path(__file__).resolve().parent.parent
CASE_NAME = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "demo"
KEEP = "--keep" in sys.argv
CASE_DIR = ROOT / "eval" / "cases" / CASE_NAME
RESULT_DIR = ROOT / "eval" / "results"


def call(method, path, token=None, body=None, raw=None, headers_extra=None, timeout=180):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers_extra:
        h.update(headers_extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else raw
    # 评测跑数分钟，容器重建/瞬时抖动会掐断连接 → 重试而非直接崩
    last = None
    for attempt in range(4):
        req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
            return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "detail": e.read().decode("utf-8", "replace")[:300]}
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{method} {path} 重试后仍失败：{last}")


def upload(token, project_id, path: Path, doc_role: str, bidder_id=None):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    if bidder_id is not None:
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    ctype = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode() + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{project_id}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def wait_job(token, job_id, label):
    for _ in range(900):
        time.sleep(1.5)
        job = call("GET", f"/jobs/{job_id}", token)
        if job.get("status") in ("succeeded", "failed"):
            if job["status"] == "failed":
                print(f"  [失败] {label}: {(job.get('error') or '')[-300:]}")
            return job
    print(f"  [超时] {label}")
    return {"status": "timeout"}


def norm(s: str) -> str:
    return "".join(ch for ch in (s or "") if not ch.isspace())


def write_smoke_report(case, pid, ts, items, by_type, matrices, docs, timings):
    """真实标书通常没有标准答案 —— 输出运营指标 + 人工抽查要点。"""
    type_labels = {"qualification": "资格条件", "compliance": "符合性(否决)", "scoring": "评分项"}
    state_labels = {"passed": "通过", "failed": "不通过", "review": "待复核", "pending": "未审"}
    parse_labels = {"parsed": "已解析", "manual_required": "需人工", "failed": "失败", "pending": "待解析", "parsing": "解析中"}

    pages_total = sum(d.get("page_count") or 0 for d in docs)
    parse_stat = {}
    for d in docs:
        parse_stat[d["parse_status"]] = parse_stat.get(d["parse_status"], 0) + 1

    rows = []
    for name, m in matrices.items():
        s = m["summary"]
        ev = sum(len((r["result"] or {}).get("ai_evidence") or []) for r in m["rows"] if r["result"])
        cited_docs = {e.get("doc_name") for r in m["rows"] if r["result"]
                      for e in ((r["result"] or {}).get("ai_evidence") or [])}
        rows.append({"bidder": name, **s, "evidence": ev, "docs_referenced": sorted(d for d in cited_docs if d)})

    summary = {
        "case": case["name"], "project_id": pid, "ts": ts, "mode": "smoke",
        "items": {"total": len(items), "by_type": by_type,
                  "with_page": sum(1 for i in items if i.get("source_page")),
                  "categories": sorted({i.get("category") or "未分类" for i in items})},
        "documents": {"total": len(docs), "pages": pages_total, "by_status": parse_stat},
        "review": rows,
        "timings_sec": timings,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / f"{CASE_NAME}-{ts}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# 冒烟报告（真实标书） · {case['name']}", f"- 时间：{ts}　项目：# {pid}　（无标准答案，仅运营指标）", "",
          "## 一、条目抽取", f"- 共 **{len(items)}** 条：" +
          "、".join(f"{type_labels.get(k, k)} {v}" for k, v in by_type.items()),
          f"- 带来源页码：{sum(1 for i in items if i.get('source_page'))}/{len(items)}",
          f"- 分类覆盖：{ '、'.join(sorted({i.get('category') or '未分类' for i in items})) }", "",
          "## 二、文件解析",
          f"- 文件 {len(docs)} 个，合计 {pages_total} 页；状态：" +
          "、".join(f"{parse_labels.get(k, k)} {v}" for k, v in parse_stat.items()), "",
          "## 三、审查结论分布", "| 投标人 | 通过 | 不通过 | 待复核 | 未审 | 证据条数 |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['bidder']} | {r.get('passed', 0)} | {r.get('failed', 0)} | {r.get('review', 0)} | {r.get('pending', 0)} | {r['evidence']} |")
    md += ["", "## 四、耗时", "　".join(f"{k} {v}s" for k, v in timings.items()), "",
           "## 五、人工抽查要点",
           "1. **条目抽取是否漏项/多抽**：打开条目库逐类核对（验收标准：关键资格与否决项不漏、评分项分值正确）",
           "2. **不通过结论是否有依据**：审查工作台逐条看证据页码与原文",
           "3. **待复核占比是否过高**：比例高说明标书材料不完整或检索没找到证据，需人工接管",
           "4. **来源页码是否准确**：抽查若干条，用「查看原文」定位比对", "",
           "## 六、条目样例（前 10 条，供快速判读）", "| 类型 | 分类 | 来源页 | 条款 |", "|---|---|---|---|"]
    for i in items[:10]:
        md.append(f"| {type_labels.get(i['item_type'], i['item_type'])} | {i.get('category') or '—'} | {i.get('source_page') or '—'} | {(i['clause_text'] or '')[:60]} |")
    (RESULT_DIR / f"{CASE_NAME}-{ts}.md").write_text("\n".join(md), encoding="utf-8")

    print("\n".join(md[:20]))
    print(f"\n报告已写入 eval/results/{CASE_NAME}-{ts}.md")
    print(f"评测项目保留在系统中（#{pid}），请在界面上人工抽查。")


def main():
    if not (CASE_DIR / "case.json").exists():
        print(f"找不到样例：{CASE_DIR}/case.json（可用 scripts/new_case.py 建脚手架）")
        sys.exit(1)
    case = json.loads((CASE_DIR / "case.json").read_text(encoding="utf-8"))
    ts = time.strftime("%Y%m%d-%H%M%S")
    smoke = not case.get("expected_items") and not case.get("expected_reviews")
    print(f"== 评测样例：{case['name']} ==" + ("（未提供标准答案 → 冒烟模式：只出运营指标）" if smoke else ""))

    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    t_start = time.time()
    proj = call("POST", "/projects", token, {
        "name": f"评测-{CASE_NAME}-{ts}", "type": "goods", "method_type": "comprehensive",
        "control_price": case.get("control_price"), "description": "评测集自动创建",
    })
    pid = proj["id"]
    print(f"项目已建 #{pid}")

    # 1) 招标文件 → 条目抽取
    t0 = time.time()
    t = upload(token, pid, CASE_DIR / case["tender"], "tender")
    wait_job(token, t["job_id"], "招标文件解析")
    ext = call("POST", f"/projects/{pid}/requirement-items/extract", token)
    job = wait_job(token, ext["job_id"], "条目抽取")
    extract_sec = time.time() - t0
    print(f"  抽取结果: {json.dumps(job.get('result'), ensure_ascii=False)}")

    items = call("GET", f"/projects/{pid}/requirement-items", token)["items"]
    for it in items:  # 自动确认（评测场景不需要人工在环）
        call("PATCH", f"/projects/{pid}/requirement-items/{it['id']}", token, {"expert_confirmed": True})
    print(f"  已确认条目 {len(items)} 条")

    # 2) 投标文件（真实场景投标文件不公开，允许只跑招标文件侧）
    t0 = time.time()
    bidder_ids = {}
    for name, files in case.get("bids", {}).items():
        b = call("POST", f"/projects/{pid}/bidders", token, {"name": name})
        bidder_ids[name] = b["id"]
        for fn in files:
            r = upload(token, pid, CASE_DIR / fn, "bid", b["id"])
            wait_job(token, r["job_id"], f"{name} 解析")
    parse_sec = time.time() - t0

    # 3) 审查（有投标文件才跑）
    t0 = time.time()
    matrices = {}
    if bidder_ids:
        run = call("POST", f"/projects/{pid}/review/run", token, {})
        for j in run.get("jobs", []):
            wait_job(token, j["job_id"], f"审查-{j['bidder_name']}")
        matrices = {name: call("GET", f"/projects/{pid}/review/matrix?bidder_id={bid}", token)
                    for name, bid in bidder_ids.items()}
    else:
        print("  （未提供投标文件，跳过审查阶段：只评估招标文件侧）")
    review_sec = time.time() - t0

    docs = call("GET", f"/projects/{pid}/documents", token)["documents"]
    by_type = {t: sum(1 for i in items if i["item_type"] == t) for t in ("qualification", "compliance", "scoring")}
    timings = {"抽取阶段": round(extract_sec, 1), "投标文件解析": round(parse_sec, 1), "审查阶段": round(review_sec, 1),
               "合计": round(time.time() - t_start, 1)}

    if smoke:
        write_smoke_report(case, pid, ts, items, by_type, matrices, docs, timings)
        return

    # ---- 指标 1：条目抽取 ----
    matched_expected = set()
    used_items = set()
    page_ok = page_total = 0
    for ei, exp in enumerate(case["expected_items"]):
        for it in items:
            if it["item_type"] != exp["type"] or it["id"] in used_items:
                continue
            if exp["clause_contains"] in norm(it["clause_text"]):
                used_items.add(it["id"])
                matched_expected.add(ei)
                page_total += 1
                if it.get("source_page") and abs(it["source_page"] - exp["page"]) <= 1:
                    page_ok += 1
                break
    recall = len(matched_expected) / len(case["expected_items"]) if case["expected_items"] else 0
    precision = len(used_items) / len(items) if items else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    # ---- 指标 2：判定命中 ----
    review_rows, leaks, false_fails, pending, correct = [], 0, 0, 0, 0
    for exp in case["expected_reviews"]:
        target = next((it for it in items
                       if exp["clause_contains"] in norm(it["clause_text"])
                       and it["item_type"] in ("compliance", "qualification")), None)
        if target is None:
            review_rows.append({**exp, "ai_state": None, "verdict": "条目未抽到"})
            continue
        row = next((r for r in matrices[exp["bidder"]]["rows"] if r["item"]["id"] == target["id"]), None)
        ai = (row or {}).get("result", {}).get("ai_state") if row and row.get("result") else None
        if ai == exp["state"]:
            verdict, correct = "命中", correct + 1
        elif ai == "review":
            verdict, pending = "待复核（未误判）", pending + 1
        elif exp["state"] == "failed" and ai == "passed":
            verdict, leaks = "漏判（该否决未否决）", leaks + 1
        elif exp["state"] == "passed" and ai == "failed":
            verdict, false_fails = "误判（不该否决却否决）", false_fails + 1
        else:
            verdict = f"未判定（{ai}）"
        review_rows.append({**exp, "ai_state": ai, "verdict": verdict})

    decisive = len(case["expected_reviews"])
    accuracy = correct / decisive if decisive else 0

    # ---- 指标 3：引文准确率 ----
    cite_rows, cite_ok = [], 0
    for exp in case.get("expected_evidence", []):
        target = next((it for it in items
                       if exp["clause_contains"] in norm(it["clause_text"])
                       and it["item_type"] in ("compliance", "qualification")), None)
        row = next((r for r in matrices[exp["bidder"]]["rows"] if r["item"]["id"] == target["id"]), None) if target else None
        pages = [e.get("page") for e in ((row or {}).get("result", {}) or {}).get("ai_evidence", [])] if row else []
        hit = any(p and abs(p - exp["page"]) <= 1 for p in pages)
        cite_ok += 1 if hit else 0
        cite_rows.append({**exp, "cited_pages": sorted({p for p in pages if p}), "ok": hit})
    cite_acc = cite_ok / len(case["expected_evidence"]) if case.get("expected_evidence") else None

    # ---- 报告 ----
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "case": case["name"], "project_id": pid, "ts": ts,
        "extract": {"expected": len(case["expected_items"]), "matched": len(matched_expected),
                    "extracted_total": len(items), "recall": round(recall, 3), "precision": round(precision, 3),
                    "f1": round(f1, 3), "page_acc": round(page_ok / page_total, 3) if page_total else None},
        "review": {"decisive": decisive, "correct": correct, "accuracy": round(accuracy, 3),
                   "leaks": leaks, "false_fails": false_fails, "pending": pending},
        "citation": {"checked": len(case.get("expected_evidence", [])), "ok": cite_ok,
                     "accuracy": round(cite_acc, 3) if cite_acc is not None else None},
        "rows": review_rows, "cite_rows": cite_rows,
    }
    (RESULT_DIR / f"{CASE_NAME}-{ts}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# 评测报告 · {case['name']}", f"- 时间：{ts}　项目：# {pid}", ""]
    e, r = summary["extract"], summary["review"]
    md += ["## 一、条目抽取",
           f"- 标准答案 {e['expected']} 条，命中 {e['matched']} 条 → **召回 {e['recall']:.0%}**",
           f"- 实际抽取 {e['extracted_total']} 条，其中命中标准答案 {e['matched']} 条（{e['precision']:.0%}）",
           "  - 注：标准答案是「必备条目」清单，非穷尽清单；未在清单内的抽取条目可能是招标文件中"
           "其他有效条款（如另列的有效期/保证金条目），需人工抽查确认而非一律计为错误",
           f"- 来源页码准确（±1 页）：**{e['page_acc']:.0%}**" if e["page_acc"] is not None else "- 来源页码：无匹配",
           "", "## 二、审查判定",
           f"- 判定标准答案 {r['decisive']} 项，命中 {r['correct']} 项 → **准确率 {r['accuracy']:.0%}**",
           f"- ⚠️ 漏判（该否决未否决）：**{r['leaks']}**", f"- ⚠️ 误判（不该否决却否决）：**{r['false_fails']}**",
           f"- 转待复核（未误判，需人工）：{r['pending']}", ""]
    if summary["citation"]["checked"]:
        md += ["## 三、引文准确率", f"- 抽查 {summary['citation']['checked']} 项，命中 {cite_ok} 项 → **{cite_acc:.0%}**", ""]
    md += ["## 四、判定明细", "| 投标人 | 条目 | 期望 | AI | 结论 |", "|---|---|---|---|---|"]
    for row in review_rows:
        md.append(f"| {row['bidder']} | {row['clause_contains']} | {row['state']} | {row.get('ai_state') or '—'} | {row['verdict']} |")
    (RESULT_DIR / f"{CASE_NAME}-{ts}.md").write_text("\n".join(md), encoding="utf-8")

    print("\n".join(md[:14]))
    print(f"\n报告已写入 eval/results/{CASE_NAME}-{ts}.md")
    print(f"评测项目保留在系统中（#{pid}），可人工复核。")


if __name__ == "__main__":
    main()
