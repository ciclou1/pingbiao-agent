"""M2 端到端验证脚本（在 api 容器内运行）：生成 3 家投标文件 → 上传 → 确认条目 → 跑审查 → 打印矩阵。

用法：docker compose -p pingbiao cp scripts/m2_seed.py api:/tmp/m2_seed.py
      docker compose -p pingbiao exec -T api python /tmp/m2_seed.py
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

BIDDERS = {
    "投标人甲": [
        ("投标函", "投标人甲（宁波海达机电设备有限公司）投标总报价：108.00万元。我方承诺投标有效期为90日，自开标之日起计算。已缴纳投标保证金2万元。投标文件已按要求密封并在封口处加盖公章。"),
        ("营业执照及资质", "统一社会信用代码：91330200MA2XXXXXX，注册资本：500万元。本公司具有独立法人资格，持有有效营业执照，经营范围包含机电设备及备件销售。"),
        ("类似业绩", "近年类似供货业绩清单：1) 2024年平海电厂输煤系统备件供货合同，合同金额86万元，已验收；2) 2024年嘉兴电厂输煤皮带备件供货项目，合同金额52万元，已验收；3) 2025年温州电厂输煤系统滚筒备件供货，合同金额41万元，已验收。共计提供3个火电厂输煤系统备件供货业绩，合同复印件及中标通知书见附件。"),
        ("财务状况", "本公司财务状况良好，资产负债率45%。提供2023年度、2024年度、2025年度经审计的财务报告（见附件）。"),
        ("项目负责人", "拟派项目负责人张建国，从事火电厂备件供应工作8年，主持过平海电厂、嘉兴电厂等多个输煤系统备件供货项目。"),
        ("体系认证", "本公司已通过ISO9001质量管理体系认证，证书编号：00124Q31285R0M，有效期至2027年6月。"),
    ],
    "投标人乙": [
        ("投标函", "投标人乙（杭州通用机电有限公司）投标总报价：112.00万元。我方投标有效期为60日，自开标之日起计算。已缴纳投标保证金2万元。投标文件已密封并加盖公章。"),
        ("营业执照及资质", "统一社会信用代码：91330100MA2YYYYYY，注册资本：300万元。具有独立法人资格，持有效营业执照。"),
        ("类似业绩", "近年类似供货业绩：2024年完成某电厂输煤系统备件供货业绩1个，合同金额35万元，已验收（合同复印件见附件）。"),
        ("财务状况", "财务状况良好，提供2023年度、2024年度、2025年度经审计的财务报告。"),
        ("体系认证", "已通过ISO9001质量管理体系认证，证书编号：00124Q99887R0M。"),
    ],
    "投标人丙": [
        ("投标函", "投标人丙（绍兴重工设备有限公司）投标总报价：125.00万元。我方承诺投标有效期90日，自开标之日起计算。投标文件已密封并加盖公章。"),
        ("营业执照及资质", "统一社会信用代码：91330500MA2ZZZZZZ，注册资本：800万元。具有独立法人资格，持有效营业执照。"),
        ("类似业绩", "近年类似供货业绩：2024年台州电厂输煤系统备件供货（合同金额60万元，已验收）；2025年宁波电厂输煤皮带托辊备件供货（合同金额44万元，已验收）。共2个业绩。"),
        ("财务状况", "财务状况良好，提供经审计的2023-2025年度财务报告。"),
        ("体系认证", "已通过ISO9001质量管理体系认证。"),
    ],
}


def call(method, path, token=None, body=None, raw=None, headers_extra=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else raw
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_status": e.code, **json.loads(e.read().decode("utf-8"))}


def make_pdf(pages: list[tuple[str, str]]) -> bytes:
    import fitz

    doc = fitz.open()
    for title, body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), title, fontname="china-s", fontsize=16)
        page.insert_textbox(fitz.Rect(72, 100, 520, 760), body, fontname="china-s", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def upload(token, bidder_id, filename, pdf_bytes, doc_role="bid"):
    boundary = uuid.uuid4().hex
    body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=body,
                headers_extra={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def wait_job(token, job_id, label, timeout=900):
    last = None
    for _ in range(timeout // 2):
        time.sleep(2)
        job = call("GET", f"/jobs/{job_id}", token)
        msg = f"{job.get('status')} {job.get('progress')}% {job.get('message','')}"
        if msg != last:
            print(f"   [{label}] {msg}")
            last = msg
        if job.get("status") in ("succeeded", "failed"):
            if job["status"] == "failed":
                print(f"   [{label}] error: {(job.get('error') or '')[-300:]}")
            return job
    return {"status": "timeout"}


def main():
    login = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    token = login["token"]
    print("login ok")

    # 1) 投标人
    existing = call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]
    by_name = {b["name"]: b["id"] for b in existing}
    for name in BIDDERS:
        if name not in by_name:
            r = call("POST", f"/projects/{PROJECT_ID}/bidders", token, {"name": name, "package_no": "1"})
            by_name[name] = r["id"]
    print("bidders:", by_name)

    # 2) 上传投标文件
    for name, pages in BIDDERS.items():
        pdf = make_pdf(pages)
        r = upload(token, by_name[name], f"{name}_投标文件.pdf", pdf)
        print(f"upload {name}: doc={r.get('id')} job={r.get('job_id')} {r.get('detail','')}")
        wait_job(token, r["job_id"], f"parse-{name}")

    # 3) 确认全部条目（人工在环：确认后才作为评审依据）
    items = call("GET", f"/projects/{PROJECT_ID}/requirement-items", token)["items"]
    todo = [i for i in items if not i["expert_confirmed"]]
    for it in todo:
        call("PATCH", f"/projects/{PROJECT_ID}/requirement-items/{it['id']}", token, {"expert_confirmed": True})
    print(f"confirmed items: {len(todo)} (total {len(items)})")

    # 4) 跑审查
    run = call("POST", f"/projects/{PROJECT_ID}/review/run", token, {})
    print("review jobs:", run)
    for j in run.get("jobs", []):
        job = wait_job(token, j["job_id"], f"review-{j['bidder_name']}")
        print(f"   result: {json.dumps(job.get('result'), ensure_ascii=False)}")

    # 5) 打印矩阵
    for name, bid in by_name.items():
        m = call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bid}", token)
        print(f"\n===== {name} summary: {json.dumps(m['summary'], ensure_ascii=False)} =====")
        for row in m["rows"]:
            it, res = row["item"], row["result"]
            if res is None:
                print(f"  [未审] {it['clause_text'][:34]}")
                continue
            check = ",".join(res.get("ai_check", {}).get("fired", []))
            ev = ",".join(str(e.get("page")) for e in (res.get("ai_evidence") or [])[:3])
            print(f"  [AI:{res['ai_state']:6s} 专家:{res['expert_state'] or '-':6s} conf={res['ai_confidence']}] "
                  f"{it['clause_text'][:30]} | {res['ai_reason'][:44]} | p{ev} | {check}")


if __name__ == "__main__":
    main()
