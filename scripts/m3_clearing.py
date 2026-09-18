"""M3 清标 + 围标串标预警验证（api 容器内运行）。

构造三家报价表：
  甲 880,000 元（合规）
  乙 915,000 元（第 4 行数量×单价≠合价 → 算术错误；托辊单价与甲相同）
  丙 1,250,000 元（超控制价 120 万；托辊单价偏高 + 与甲/乙同价）
用法：docker compose -p pingbiao cp scripts/m3_clearing.py api:/tmp/ && docker compose -p pingbiao exec -T api python /tmp/m3_clearing.py
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:8000/api/v1"
PROJECT_ID = 1

SHEETS = {
    "投标人甲": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 100, 2000, 200000],
        [2, "托辊", "个", 200, 500, 100000],
        [3, "滚筒", "个", 50, 8000, 400000],
        [4, "轴承", "套", 300, 600, 180000],
        ["合计", "", "", "", "", 880000],
    ],
    "投标人乙": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 100, 2100, 210000],
        [2, "托辊", "个", 200, 500, 100000],
        [3, "滚筒", "个", 50, 8500, 425000],
        [4, "轴承", "套", 300, 620, 180000],  # 应为 186000 → 算术错误
        ["合计", "", "", "", "", 915000],
    ],
    "投标人丙": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 100, 2400, 240000],
        [2, "托辊", "个", 200, 1000, 200000],  # 单价偏高，且与甲/乙“托辊”同名
        [3, "滚筒", "个", 50, 9000, 450000],
        [4, "轴承", "套", 300, 1200, 360000],
        ["合计", "", "", "", "", 1250000],  # 125 万 > 控制价 120 万
    ],
}


def call(method, path, token=None, body=None, raw=None, headers_extra=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers_extra:
        h.update(headers_extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else raw
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read().decode("utf-8"))}


def make_xlsx(rows: list) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "报价清单"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(token, bidder_id, filename, blob, doc_role):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + blob + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    bidders = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}

    for name, rows in SHEETS.items():
        r = upload(token, bidders[name], f"{name}_报价清单.xlsx", make_xlsx(rows), "price")
        if r.get("_status"):
            print(f"上传 {name} 失败: {r}")
            continue
        for _ in range(30):
            time.sleep(1)
            job = call("GET", f"/jobs/{r['job_id']}", token)
            if job["status"] in ("succeeded", "failed"):
                print(f"{name} 报价表解析: {job['status']} {json.dumps(job.get('result'), ensure_ascii=False)}")
                break

    items = call("GET", f"/projects/{PROJECT_ID}/clearing/items", token)
    print("\n导入明细:", [(b["bidder_name"], b["count"]) for b in items.get("by_bidder", [])])

    print("\n=== 运行清标 ===")
    print(call("POST", f"/projects/{PROJECT_ID}/clearing/run", token))

    print("\n=== 运行围标串标预警 ===")
    print(call("POST", f"/projects/{PROJECT_ID}/collusion/run", token))

    alerts = call("GET", f"/projects/{PROJECT_ID}/alerts", token)["alerts"]
    print(f"\n=== 预警清单（{len(alerts)} 条）===")
    for a in alerts:
        detail = a["detail"]
        extra = ""
        if a["code"] == "arithmetic":
            extra = str(detail["rows"][0])
        elif a["code"] == "over_limit":
            extra = f"{detail['total']:,.0f} > {detail['limit']:,.0f}（表内口径 {detail.get('assumed_unit')}）"
        elif a["code"] == "unbalanced":
            extra = str(detail["rows"][0])
        elif a["code"] == "similarity":
            extra = f"汉明距离 {detail.get('hamming')} 相似度 {detail.get('similarity')}"
        elif a["code"] == "price_pattern":
            extra = str(detail.get("rows", [{}])[0]) if detail.get("rows") else str(detail.get("totals"))
        elif a["code"] == "meta":
            extra = detail.get("value", "") or f"{detail.get('count', '')} 对"
        print(f"[{a['level']:6s}] {a['code']:13s} {a['title']}\n          {extra}")


if __name__ == "__main__":
    main()
