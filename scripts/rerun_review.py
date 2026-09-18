"""重跑指定投标人的审查并打印矩阵（宿主机运行，走 nginx；容器重建不中断本脚本）。

用法：PROJECT_ID=4 python scripts/rerun_review.py 投标人甲 投标人乙 [--clearing]
  不带投标人名称=只打印矩阵；--clearing 结束时顺带跑清标与围标预警
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:5180/api/v1"
PROJECT_ID = int(os.environ.get("PROJECT_ID", "1"))


def call(method, path, token=None, body=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    d = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=d, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read().decode("utf-8"))}


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    bidders = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]

    jobs = []
    for name in targets:
        run = call("POST", f"/projects/{PROJECT_ID}/review/run", token, {"bidder_id": bidders[name]})
        if run.get("_status"):
            print(f"trigger {name} failed: {run}")
            continue
        for j in run["jobs"]:
            print(f"triggered {j['bidder_name']} -> job {j['job_id']}", flush=True)
            jobs.append(j)

    for j in jobs:
        last = None
        for _ in range(1200):
            time.sleep(3)
            job = call("GET", f"/jobs/{j['job_id']}", token)
            msg = f"{job.get('status')} {job.get('progress')}% {job.get('message','')}"
            if msg != last:
                print(f"  [{j['bidder_name']}] {msg}", flush=True)
                last = msg
            if job.get("status") in ("succeeded", "failed"):
                print(f"  result: {json.dumps(job.get('result'), ensure_ascii=False)}", flush=True)
                if job["status"] == "failed":
                    print(f"  ERROR: {(job.get('error') or '')[-400:]}", flush=True)
                break

    if "--clearing" in sys.argv:
        print("\n清标:", call("POST", f"/projects/{PROJECT_ID}/clearing/run", token))
        print("围标预警:", call("POST", f"/projects/{PROJECT_ID}/collusion/run", token))
        alerts = call("GET", f"/projects/{PROJECT_ID}/alerts", token)["alerts"]
        print(f"\n===== 清标与预警 {len(alerts)} 条 =====")
        for a in alerts:
            print(f"  [{a['level']:6s}] {a['code']:13s} {a['title']}")

    for name, bid in bidders.items():
        if targets and name not in targets:
            continue
        m = call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bid}", token)
        print(f"\n===== {name} 汇总 {json.dumps(m['summary'], ensure_ascii=False)} =====", flush=True)
        for row in m["rows"]:
            it, res = row["item"], row["result"]
            if res is None:
                continue
            if "failed" not in (res["ai_state"],) and "--all" not in sys.argv:
                continue
            fired = ",".join(res.get("ai_check", {}).get("fired", []))
            pages = ",".join(str(e.get("page")) for e in (res.get("ai_evidence") or [])[:3])
            print(f"  [{res['ai_state']:6s} conf={res['ai_confidence']}] p{it['source_page']} "
                  f"{it['clause_text'][:44]} | {res['ai_reason'][:52]} | 证据p{pages} | {fired}", flush=True)


if __name__ == "__main__":
    main()
