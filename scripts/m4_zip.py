"""M4 验证：工程标书包（zip 容器）解包解析 + 证据带出处。

构造一个模拟工程电子标书包：内含 技术标.pdf + 商务标.docx + 报价.xml + 加密成员，
验证解包后能正常抽块、检索，且证据能标出「来自哪个成员文件」。
用法：docker compose -p pingbiao cp scripts/m4_zip.py api:/tmp/ && docker compose -p pingbiao exec -T api python /tmp/m4_zip.py
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://localhost:8000/api/v1"
PROJECT_ID = 1


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


def build_zip() -> bytes:
    import fitz
    from docx import Document as Docx

    # 技术标 PDF
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "技术标", fontname="china-s", fontsize=16)
    page.insert_textbox(
        fitz.Rect(72, 100, 520, 700),
        "我公司提供的输煤皮带采用高强度钢丝绳芯，设计寿命不低于5年。交货期承诺30日历天内到货，"
        "质保期24个月。技术方案包含设备选型说明、生产工艺、质量保证措施与售后服务方案。",
        fontname="china-s", fontsize=11,
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    # 商务标 docx
    d = Docx()
    d.add_heading("商务标", level=1)
    d.add_paragraph("投标人：投标人甲（宁波海达机电设备有限公司）")
    d.add_paragraph("注册资本：500万元；已通过ISO9001质量管理体系认证。")
    d.add_paragraph("类似业绩：2024年平海电厂输煤系统备件供货、2024年嘉兴电厂输煤皮带备件供货、2025年温州电厂滚筒备件供货，共3个。")
    buf = io.BytesIO()
    d.save(buf)
    docx_bytes = buf.getvalue()

    # 报价 XML（工程标书包常见格式）
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<报价单><项目><名称>输煤皮带</名称><单位>米</单位><数量>100</数量><单价>2000</单价><合价>200000</合价></项目>
<项目><名称>托辊</名称><单位>个</单位><数量>200</数量><单价>500</单价><合价>100000</合价></项目>
<合计>880000</合计></报价单>"""

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("投标人甲/技术标.pdf", pdf_bytes)
        zf.writestr("投标人甲/商务标.docx", docx_bytes)
        zf.writestr("投标人甲/报价表.xml", xml)
        zf.writestr("投标人甲/说明.txt", "本标书包由电子招投标平台生成，含技术标、商务标与报价表。")
        zf.writestr("投标人甲/签章.dat", b"\x00\x01\x02binary-signature-placeholder")
    return out.getvalue()


def upload(token, bidder_id, filename, blob, doc_role="bid"):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/zip\r\n\r\n".encode() + blob + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]
    bidders = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}

    blob = build_zip()
    print(f"标书包大小: {len(blob)} 字节")
    r = upload(token, bidders["投标人甲"], "投标人甲_电子标书包.zip", blob)
    print("上传:", r.get("id"), r.get("_status", ""), r.get("detail", ""))

    for _ in range(40):
        time.sleep(1)
        job = call("GET", f"/jobs/{r['job_id']}", token)
        if job["status"] in ("succeeded", "failed"):
            print("解析:", job["status"], json.dumps(job.get("result"), ensure_ascii=False))
            if job["status"] == "failed":
                print((job.get("error") or "")[-400:])
            break

    docs = call("GET", f"/projects/{PROJECT_ID}/documents", token)["documents"]
    z = next((d for d in docs if d["id"] == r.get("id")), None)
    print("文档状态:", z["original_name"], z["parse_status"], "页数(容器内顺序):", z["page_count"])

    # 解包内容抽查
    import subprocess
    chunks = call("GET", f"/projects/{PROJECT_ID}/qa/ask", token, {
        "question": "技术标中承诺的交货期和质保期分别是多少？", "scope": "bidder", "bidder_id": bidders["投标人甲"]
    })
    print("\n问答（检索自标书包）:", chunks.get("answer", chunks)[:220])
    print("引用出处:", [(c.get("doc_name"), c.get("page")) for c in chunks.get("citations", [])[:4]])


if __name__ == "__main__":
    main()
