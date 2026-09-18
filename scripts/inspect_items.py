"""条目质检：打印某个项目抽出的评审条目，便于人工抽查质量。

用法：python scripts/inspect_items.py [项目ID或"last"] [--all]
  默认只看概览 + 每类前 6 条；--all 打印全部条目
"""
import io
import json
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:5180/api/v1"
TYPE_LABELS = {"qualification": "资格条件", "compliance": "符合性(否决)", "scoring": "评分项"}

# 真实招标文件应当覆盖的关键板块（用于判断是否漏抽）
KEY_SECTIONS = {
    "资格要求": ("资格", "资质", "营业执照", "法人"),
    "业绩要求": ("业绩", "供货", "合同", "项目经验"),
    "财务要求": ("财务", "审计", "资产"),
    "否决/废标条款": ("否决", "废标", "无效投标"),
    "报价与限价": ("报价", "限价", "控制价", "价格分"),
    "保证金": ("保证金", "担保"),
    "有效期": ("有效期",),
    "评分细则": ("评分", "得分", "分值"),
    "技术/服务要求": ("技术", "方案", "售后", "质保", "交货"),
}


def call(method, path, token=None, body=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    d = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=d, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "detail": e.read().decode("utf-8", "replace")[:200]}


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    arg = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "last"
    if arg == "last":
        projects = call("GET", "/projects", token)["projects"]
        pid = projects[0]["id"]
        pname = projects[0]["name"]
    else:
        pid = int(arg)
        pname = next((p["name"] for p in call("GET", "/projects", token)["projects"] if p["id"] == pid), str(pid))

    data = call("GET", f"/projects/{pid}/requirement-items", token)
    items = data["items"]
    print(f"项目 #{pid} {pname}")
    print(f"条目总数 {len(items)}：", "，".join(f"{TYPE_LABELS.get(k, k)} {v['total']}" for k, v in data["summary"].items()))

    with_page = sum(1 for i in items if i.get("source_page"))
    print(f"带来源页码：{with_page}/{len(items)}（{with_page / len(items):.0%}）" if items else "（无条目）")
    cats = {}
    for i in items:
        cats[i.get("category") or "未分类"] = cats.get(i.get("category") or "未分类", 0) + 1
    print("分类分布：", "，".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))

    print("\n=== 关键板块覆盖检查 ===")
    for section, keywords in KEY_SECTIONS.items():
        hit = [i for i in items if any(k in (i["clause_text"] or "") + (i.get("requirement") or "") for k in keywords)]
        mark = "✓" if hit else "✗"
        print(f"  {mark} {section}：{len(hit)} 条")

    print("\n=== 页码分布 ===")
    pages = sorted({i.get("source_page") for i in items if i.get("source_page")})
    print("  来源页:", pages[:40], "..." if len(pages) > 40 else "")

    show_all = "--all" in sys.argv
    print("\n=== 条目明细 ===" if show_all else "\n=== 条目样例（每类前 6 条）===")
    for t in ("qualification", "compliance", "scoring"):
        sub = [i for i in items if i["item_type"] == t]
        if not sub:
            continue
        print(f"\n【{TYPE_LABELS[t]}】共 {len(sub)} 条")
        for i in (sub if show_all else sub[:6]):
            score = f" 分值{i['score']}" if i.get("score") is not None else ""
            print(f"  p{i.get('source_page') or '—'}{score} [{i.get('category') or '—'}] {(i['clause_text'] or '')[:78]}")
            if i.get("requirement"):
                print(f"        要求：{i['requirement'][:70]}")


if __name__ == "__main__":
    main()
