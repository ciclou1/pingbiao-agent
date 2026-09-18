"""OCR 验证：① 直接打 OCR 服务（测识别质量与速度）② 全链路（扫描件上传 → 解析 → 检索）

用法：
  python scripts/ocr_test.py direct        # 只测 OCR 服务
  python scripts/ocr_test.py e2e           # 全链路（需先启动 ocr 服务并置 OCR_ENABLED=true）
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

API = "http://localhost:5180/api/v1"
OCR = "http://localhost:8800"
SCAN_DIR = Path(__file__).resolve().parent.parent / "eval" / "cases" / "scanned"
PROJECT_ID = 1

# 期望识别出的关键内容（人工核对用）
EXPECT_KEYS = ["投标人甲", "108.00万元", "90日", "投标保证金2万元", "91330200MA2XXXXXX"]


def call(method, path, token=None, body=None, raw=None, headers_extra=None, timeout=300, base=API):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers_extra:
        h.update(headers_extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else raw
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "detail": e.read().decode("utf-8", "replace")[:300]}


def test_direct():
    png = (SCAN_DIR / "page.png").read_bytes()
    print(f"图片 {len(png)} 字节 → POST {OCR}/recognize")
    t0 = time.time()
    res = call("POST", "/recognize", raw=png, headers_extra={"Content-Type": "image/png"}, base=OCR)
    wall = time.time() - t0
    if res.get("_status"):
        print("调用失败：", res)
        return
    print(f"引擎：{res.get('engine')}　行数：{res.get('lines')}　服务内耗时：{res.get('ms')}ms　端到端：{wall:.1f}s")
    text = res.get("text", "")
    print("\n--- 识别结果 ---")
    print(text)
    print("\n--- 关键内容核对 ---")
    for k in EXPECT_KEYS:
        print(f"  {'✓' if k in text else '✗'} {k}")


def upload(token, path: Path, doc_role, bidder_id):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n".encode() + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def test_e2e():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    pdf = SCAN_DIR / "扫描件_投标人甲.pdf"
    bidders = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}
    r = upload(token, pdf, "bid", bidders["投标人甲"])
    print(f"上传扫描件：doc={r.get('id')} job={r.get('job_id')} {r.get('detail','')}")
    for _ in range(180):
        time.sleep(2)
        job = call("GET", f"/jobs/{r['job_id']}", token)
        if job.get("status") in ("succeeded", "failed"):
            print("解析：", job["status"], json.dumps(job.get("result"), ensure_ascii=False))
            if job["status"] == "failed":
                print((job.get("error") or "")[-400:])
            break
    docs = call("GET", f"/projects/{PROJECT_ID}/documents", token)["documents"]
    z = next((d for d in docs if d["id"] == r.get("id")), None)
    print(f"文档：{z['original_name']}　状态={z['parse_status']}　页数={z['page_count']}")

    st = call("POST", f"/projects/{PROJECT_ID}/qa/ask", token,
              {"question": "该投标人的投标总报价、投标有效期和保证金分别是多少？", "scope": "bidder",
               "bidder_id": bidders["投标人甲"]})
    print("\n问答（检索自 OCR 文本）：", st.get("answer", st)[:300])
    print("引文：", [(c.get("doc_name"), f"p{c.get('page')}") for c in st.get("citations", [])[:3]])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "direct"
    test_direct() if mode == "direct" else test_e2e()
