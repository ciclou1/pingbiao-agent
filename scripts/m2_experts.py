"""M2 专家流程验证（宿主机运行，走 nginx）：
1. 专家确认审查终值（含"不通过必须填理由"校验）
2. 专家打分（超分值上限被拒）
3. 打分隔离：另一位专家看不到我的分；专家访问汇总接口 403；组长可汇总
"""
import io
import json
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:5180/api/v1"
PROJECT_ID = 1


def call(method, path, token=None, body=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    d = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=d, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def login(u, p):
    st, d = call("POST", "/auth/login", body={"username": u, "password": p})
    assert st == 200, (u, st, d)
    return d["token"]


admin = login("admin", "admin123")
zhang = login("zhang_expert", "pass123")
li = login("li_expert", "pass123")
wang = login("wang_leader", "pass123")
print("logins ok")

_, bidders = call("GET", f"/projects/{PROJECT_ID}/bidders", admin)
bid = bidders["bidders"][0]
bidder_id = bid["id"]

# --- 1) 专家终值 ---
_, m = call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bidder_id}", zhang)
rows = [r for r in m["rows"] if r["result"]]
print(f"matrix rows with result: {len(rows)}")
target = rows[0]
rid = target["result"]["id"]

st, d = call("PATCH", f"/projects/{PROJECT_ID}/review/results/{rid}", zhang, {"expert_state": "failed", "expert_reason": ""})
print(f"failed without reason -> {st} (expect 400)")

st, d = call("PATCH", f"/projects/{PROJECT_ID}/review/results/{rid}", zhang,
             {"expert_state": "passed", "expert_reason": "复核投标文件第1页投标函，满足要求"})
print(f"expert verdict save -> {st} state={d.get('expert_state')}")

# --- 2) 打分 ---
_, sheet = call("GET", f"/projects/{PROJECT_ID}/score/sheet?bidder_id={bidder_id}", zhang)
srows = sheet["rows"]
print(f"score sheet rows: {len(srows)}, my_progress={sheet['my_progress']}")
if srows:
    it = srows[0]["item"]
    cap = it["score"]
    st, d = call("PUT", f"/projects/{PROJECT_ID}/score", zhang,
                 {"item_id": it["id"], "bidder_id": bidder_id, "score": (cap or 10) + 5, "comment": "超上限测试"})
    print(f"over-max score -> {st} (expect 400): {d.get('detail')}")

    st, d = call("PUT", f"/projects/{PROJECT_ID}/score", zhang,
                 {"item_id": it["id"], "bidder_id": bidder_id, "score": round((cap or 10) * 0.8, 1), "comment": "响应完整，符合评分细则"})
    print(f"score save -> {st} {d}")

    # 改分留痕
    st, d = call("PUT", f"/projects/{PROJECT_ID}/score", zhang,
                 {"item_id": it["id"], "bidder_id": bidder_id, "score": round((cap or 10) * 0.7, 1),
                  "comment": "复核后下调", "reason": "重新核对技术方案第3页，扣减偏离项"})
    print(f"score change -> {st} previous={d.get('previous')} new={d.get('score')}")

    # --- 3) 隔离 ---
    _, li_sheet = call("GET", f"/projects/{PROJECT_ID}/score/sheet?bidder_id={bidder_id}", li)
    li_row = next((r for r in li_sheet["rows"] if r["item"]["id"] == it["id"]), None)
    print(f"isolation: 张 scored, 李 sees = {li_row['my_score'] if li_row else 'n/a'} (expect None)")

    st, d = call("GET", f"/projects/{PROJECT_ID}/score/summary", zhang)
    print(f"expert summary access -> {st} (expect 403)")
    st, d = call("GET", f"/projects/{PROJECT_ID}/score/summary", wang)
    if st == 200:
        b0 = d["bidders"][0]
        first = b0["items"][0] if b0["items"] else None
        print(f"leader summary -> {st}, experts_total={d['experts_total']}, first item avg={first['average'] if first else None}, "
              f"scores={[ (s['expert_name'], s['score']) for s in (first['scores'] if first else []) ]}")
    else:
        print(f"leader summary -> {st} {d}")
