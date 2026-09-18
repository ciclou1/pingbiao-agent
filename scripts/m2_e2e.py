"""M1→M2 全链路端到端验证（在 api 容器内运行）。

招标文件 → 抽取条目 → 确认 → 上传 3 家投标文件 → 审查 → 打印矩阵。
用法：docker compose -p pingbiao cp scripts/m2_e2e.py api:/tmp/m2_e2e.py
      docker compose -p pingbiao exec -T api python /tmp/m2_e2e.py
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

TENDER = [
    ("第一章 招标公告", "平海电厂2026年度输煤皮带备件采购项目，资金来源自筹。本项目预算金额120万元，最高限价118万元。"),
    ("第二章 投标人资格要求", "1. 投标人须具有独立法人资格，持有效营业执照。2. 具有近三年（2023年至今）至少2个火电厂输煤系统备件供货业绩（提供合同复印件及中标通知书）。3. 财务状况良好，提供2023-2025年度经审计的财务报告。4. 拟派项目负责人须具有5年以上火电厂备件供应经验。5. 通过ISO9001质量管理体系认证。"),
    ("第三章 评标办法前附表", "本项目采用综合评分法。评标委员会按照评分细则打分。投标保证金：人民币2万元，须在开标前缴纳。投标有效期：自开标之日起90日。"),
    ("第四章 符合性审查条款", "1. 投标文件未按要求签字盖章的，否决其投标。2. 投标报价超过最高限价118万元的，否决其投标。3. 投标有效期不足90日的，否决其投标。4. 投标保证金未按时足额缴纳的，否决其投标。5. 投标人被列入失信被执行人名单的，否决其投标。"),
    ("第五章 评分细则", "评分满分100分：（1）投标报价（40分）：以通过符合性审查的最低投标报价为基准价，报价每高于基准价1%扣1分，扣完为止。（2）类似业绩（20分）：近三年每个火电厂输煤系统业绩得4分，最多20分。（3）技术方案（25分）：方案完整性10分、交货期承诺8分、售后服务7分。（4）企业资质信誉（15分）：ISO9001认证5分，近三年无不良信用记录5分，银行资信证明5分。"),
    ("第六章 投标文件格式", "投标文件应包含：商务标、技术标、报价表。密封要求：正本一份副本四份，密封袋封口加盖公章。"),
]

BIDS = {
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


def make_pdf(pages):
    import fitz

    doc = fitz.open()
    for title, body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), title, fontname="china-s", fontsize=16)
        page.insert_textbox(fitz.Rect(72, 100, 520, 760), body, fontname="china-s", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def upload(token, filename, pdf, doc_role="bid", bidder_id=None):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    if bidder_id is not None:
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/pdf\r\n\r\n".encode()
        + pdf
        + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def wait(token, job_id, label, timeout=900):
    last = None
    for _ in range(timeout // 2):
        time.sleep(2)
        job = call("GET", f"/jobs/{job_id}", token)
        msg = f"{job.get('status')} {job.get('progress')}% {job.get('message','')}"
        if msg != last:
            print(f"   [{label}] {msg}", flush=True)
            last = msg
        if job.get("status") in ("succeeded", "failed"):
            if job["status"] == "failed":
                print(f"   [{label}] ERROR: {(job.get('error') or '')[-400:]}", flush=True)
            return job
    return {"status": "timeout"}


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]

    # 1) 招标文件
    t = upload(token, "招标文件_平海电厂输煤备件.pdf", make_pdf(TENDER), doc_role="tender")
    print("tender upload:", t.get("id"), t.get("detail", ""), flush=True)
    wait(token, t["job_id"], "tender-parse")

    # 2) 条目抽取 + 确认
    ext = call("POST", f"/projects/{PROJECT_ID}/requirement-items/extract", token)
    job = wait(token, ext["job_id"], "extract")
    print("   extract result:", json.dumps(job.get("result"), ensure_ascii=False), flush=True)

    items = call("GET", f"/projects/{PROJECT_ID}/requirement-items", token)["items"]
    for it in items:
        if not it["expert_confirmed"]:
            call("PATCH", f"/projects/{PROJECT_ID}/requirement-items/{it['id']}", token, {"expert_confirmed": True})
    print(f"   items confirmed: {len(items)}", flush=True)

    # 3) 投标人 + 投标文件
    existing = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}
    for name in BIDS:
        if name not in existing:
            existing[name] = call("POST", f"/projects/{PROJECT_ID}/bidders", token, {"name": name, "package_no": "1"})["id"]
    for name, pages in BIDS.items():
        r = upload(token, f"{name}_投标文件.pdf", make_pdf(pages), doc_role="bid", bidder_id=existing[name])
        if r.get("_status"):
            print(f"   upload {name} failed: {r}", flush=True)
            continue
        print(f"   upload {name}: doc={r['id']}", flush=True)
        wait(token, r["job_id"], f"parse-{name}")

    # 4) 审查
    run = call("POST", f"/projects/{PROJECT_ID}/review/run", token, {})
    print("review jobs:", [(j["bidder_name"], j["job_id"]) for j in run.get("jobs", [])], flush=True)
    for j in run.get("jobs", []):
        job = wait(token, j["job_id"], f"review-{j['bidder_name']}")
        print(f"   result: {json.dumps(job.get('result'), ensure_ascii=False)}", flush=True)

    # 5) 矩阵
    for name, bid in existing.items():
        m = call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bid}", token)
        print(f"\n===== {name} 汇总 {json.dumps(m['summary'], ensure_ascii=False)} =====", flush=True)
        for row in m["rows"]:
            it, res = row["item"], row["result"]
            if res is None:
                print(f"  [未审] {it['clause_text'][:34]}", flush=True)
                continue
            fired = ",".join(res.get("ai_check", {}).get("fired", []))
            pages = ",".join(str(e.get("page")) for e in (res.get("ai_evidence") or [])[:3])
            print(f"  [AI:{res['ai_state']:6s} conf={res['ai_confidence']}] {it['clause_text'][:28]} | "
                  f"{res['ai_reason'][:46]} | p{pages} | {fired}", flush=True)


if __name__ == "__main__":
    main()
