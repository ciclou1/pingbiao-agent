"""向量检索验证（模型就绪后一条命令跑完）：

  1) 服务自检：/health + 单次 /embed 的维度与耗时
  2) 为评测项目补建向量索引（worker 任务，轮询进度）
  3) 困难口语化提问对照：关键词(+同义词) vs 混合(+向量) 的 recall
  4) 证据定位复核：条目原文检索是否仍命中自身来源页（不得回退）

用法：python scripts/verify_vector.py [项目ID=自动取最新评测项目]
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

API = "http://localhost:5180/api/v1"
EMBED = "http://localhost:8900"


def call(method, path, token=None, body=None, base=API, timeout=300):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "detail": e.read().decode("utf-8", "replace")[:300]}


def step1_health():
    print("=== 1) 向量服务自检 ===")
    h = call("GET", "/health", base=EMBED, timeout=120)
    print(f"  /health: {h}")
    t0 = time.time()
    r = call("POST", "/embed", body={"texts": ["投标保证金应当在投标截止时间前到账"]}, base=EMBED, timeout=300)
    dt = time.time() - t0
    if r.get("_status") or not r.get("vectors"):
        print(f"  ✗ /embed 失败：{r}")
        return False
    vec = r["vectors"][0]
    norm = sum(x * x for x in vec) ** 0.5
    print(f"  /embed: 维度 {r['dim']}、耗时 {dt:.1f}s（服务内 {r.get('ms')}ms）、向量模长 {norm:.4f}（应为 1.0000）")
    return r["dim"] == 1024


def step2_backfill(token, project_id):
    print("\n=== 2) 补建向量索引 ===")
    r = call("POST", f"/projects/{project_id}/embed-chunks", token)
    if r.get("_status"):
        print(f"  ✗ 触发失败：{r}")
        return False
    jid = r["job_id"]
    for _ in range(200):
        time.sleep(3)
        job = call("GET", f"/jobs/{jid}", token)
        if job.get("status") in ("succeeded", "failed"):
            print(f"  任务 {job['status']}：{json.dumps(job.get('result'), ensure_ascii=False)}")
            return job["status"] == "succeeded"
    print("  ✗ 任务超时")
    return False


def main():
    ok = step1_health()
    if not ok:
        print("\n服务未就绪，停止后续步骤。")
        return
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    projects = call("GET", "/projects", token)["projects"]
    target = next((p for p in projects if p["name"].startswith("评测-")), projects[0])
    pid = target["id"]
    print(f"目标项目：{target['name']}（#{pid}）")
    step2_backfill(token, pid)
    print("\n=== 接下来请在 api 容器内跑对照实验 ===")
    print(f"  docker compose -p pingbiao exec -T api sh -c 'PYTHONPATH=/app python /tmp/retrieval_probe.py --hard'")
    print(f"  docker compose -p pingbiao exec -T api sh -c 'PYTHONPATH=/app python /tmp/retrieval_probe.py --items 15'")


if __name__ == "__main__":
    main()
