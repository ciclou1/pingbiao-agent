"""M4 并发压测：模拟 50 名专家同时评审（读为主 + 少量打分写入）。

经 nginx（含代理层）打到 api，统计各端点 p50/p95/p99 与错误率；
期间后台触发一次审查作业，验证长任务不影响前台读。

用法：python scripts/load_test.py [并发数=50] [持续秒=30]
"""
import io
import json
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:5180/api/v1"
PROJECT_ID = 1
CONCURRENCY = int(sys.argv[1]) if len(sys.argv) > 1 else 50
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0


def call(method, path, token=None, body=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    d = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=d, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = r.read()
    return json.loads(payload) if payload else {}


def login(u, p):
    return call("POST", "/auth/login", body={"username": u, "password": p})["token"]


def main():
    admin = login("admin", "admin123")
    bidders = [b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", admin)["bidders"]]
    items = call("GET", f"/projects/{PROJECT_ID}/requirement-items", admin)["items"]
    scoring = [i for i in items if i["item_type"] == "scoring"]
    print(f"并发 {CONCURRENCY}，持续 {DURATION:.0f}s；投标人 {bidders}，评分项 {len(scoring)}")

    # 用真实专家账号压测（50 人场景：多个账号各自打分）
    expert_tokens = []
    for i in range(CONCURRENCY):
        u = "zhang_expert" if i % 2 == 0 else "li_expert"
        try:
            expert_tokens.append((u, login(u, "pass123")))
        except Exception:
            expert_tokens.append(("admin", admin))

    stats: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    lock = threading.Lock()
    stop_at = time.monotonic() + DURATION

    def record(name, ms, ok=True):
        with lock:
            stats.setdefault(name, []).append(ms)
            if not ok:
                errors[name] = errors.get(name, 0) + 1

    # 读为主的操作集（权重模拟评审现场）
    def worker(idx):
        who, token = expert_tokens[idx]
        rnd = random.Random(idx)
        while time.monotonic() < stop_at:
            bid = rnd.choice(bidders)
            op = rnd.random()
            try:
                t0 = time.perf_counter()
                if op < 0.42:
                    call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bid}", token)
                    name = "GET 审查矩阵"
                elif op < 0.68:
                    call("GET", f"/projects/{PROJECT_ID}/score/sheet?bidder_id={bid}", token)
                    name = "GET 我的打分表"
                elif op < 0.82:
                    call("GET", f"/projects/{PROJECT_ID}/requirement-items", token)
                    name = "GET 条目库"
                elif op < 0.92:
                    call("GET", f"/projects/{PROJECT_ID}/qa/history", token)
                    name = "GET 问答历史"
                else:
                    it = rnd.choice(scoring) if scoring else None
                    if it is None:
                        continue
                    call("PUT", f"/projects/{PROJECT_ID}/score", token,
                         {"item_id": it["id"], "bidder_id": bid, "score": round((it["score"] or 10) * 0.8, 1),
                          "comment": "压测写入"})
                    name = "PUT 打分"
                record(name, (time.perf_counter() - t0) * 1000)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                record(name if "name" in dir() else "未知", 0, ok=False)
            time.sleep(rnd.uniform(0.05, 0.35))  # 模拟专家思考间隔

    # 后台长任务：压测期间触发一次审查，观察是否影响前台
    long_job = {"id": None, "status": "未触发"}

    def run_background_job():
        try:
            res = call("POST", f"/projects/{PROJECT_ID}/review/run", admin, {"bidder_id": bidders[0]})
            jid = res["jobs"][0]["job_id"]
            long_job["id"] = jid
            long_job["status"] = "running"
            while time.monotonic() < stop_at + 10:
                job = call("GET", f"/jobs/{jid}", admin)
                if job["status"] in ("succeeded", "failed"):
                    long_job["status"] = job["status"]
                    return
                time.sleep(2)
        except Exception as e:
            long_job["status"] = f"异常 {type(e).__name__}"

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(CONCURRENCY)]
    job_thread = threading.Thread(target=run_background_job, daemon=True)
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    job_thread.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    print(f"\n=== 压测结果（{elapsed:.1f}s）===")
    total = sum(len(v) for v in stats.values())
    err_total = sum(errors.values())
    print(f"总请求 {total}，错误 {err_total}，吞吐 {total / elapsed:.1f} req/s")
    print(f"{'端点':<16}{'次数':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'错误':>7}")
    for name, arr in sorted(stats.items()):
        if not arr:
            continue
        arr_sorted = sorted(arr)
        p = lambda q: arr_sorted[min(len(arr_sorted) - 1, int(len(arr_sorted) * q))]
        print(f"{name:<16}{len(arr):>7}{p(.5):>8.0f}ms{p(.95):>8.0f}ms{p(.99):>8.0f}ms{arr_sorted[-1]:>8.0f}ms{errors.get(name, 0):>7}")
    print(f"\n后台审查作业（压测期间并行）：{long_job['status']} (job {long_job['id']})")


if __name__ == "__main__":
    main()
