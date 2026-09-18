"""为 real1（真实 91 页政府采购招标文件）构造投标文件并跑完整评测。

投标文件内容**照真实招标文件的条款写**（信用记录、保证金到账、投标有效期、
报价与预算、电子印章、中小企业声明函），三家分别设计为：
  投标人甲：完全响应（预期：多数通过）
  投标人乙：三处硬伤（失信被执行人 ⇒ 4.4 否决；有效期 60 日 ⇒ 16.1 投标无效；保证金逾期到账 ⇒ 14.3 否决）
  投标人丙：超预算报价 ⇒ 15.4 否决；未提供信用查询结果、投进口产品 ⇒ 应转待复核

注意：投标文件由我据招标条款编写，**期望结论也是我设计的**——这验证的是管线在
真实招标文件上的机械正确性，不是独立基准准确率。

用法：docker compose -p pingbiao cp scripts/real1_bids.py api:/tmp/ && docker compose -p pingbiao exec -T api python /tmp/real1_bids.py [--no-review]
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
PROJECT_ID = 4
BUDGET = 1000  # 万元（招标文件为示范文本，预算未填；此处按评测需要设定）

BIDS = {
    "投标人甲": {
        "报价": 980,
        "pages": [
            ("投标函", "投标人：宁波海达机电设备有限公司（统一社会信用代码 91330200MA2XXXXXX）。"
                       "投标总报价：人民币玖佰捌拾万元整（980.00 万元），本项目采购预算 1000 万元，我方报价未超过预算。"
                       "投标有效期：自提交投标文件截止之日起 90 日。投标保证金：人民币 20 万元，已采用银行转账方式"
                       "于投标截止时间前到账。"),
            ("资格证明", "1. 我方持有有效营业执照，具有独立承担民事责任的能力，符合《中华人民共和国政府采购法》第二十二条规定。"
                         "2. 经查询“信用中国”网站及中国政府采购网，我方未被列入失信被执行人、重大税收违法案件当事人名单、"
                         "政府采购严重违法失信行为名单，查询截图见附件。"),
            ("中小企业声明函", "本公司为小型企业，从业人员 86 人，营业收入 3200 万元。依据《政府采购促进中小企业发展管理办法》，"
                               "现提供标准格式《中小企业声明函》，声明内容真实有效。"),
            ("商务技术响应", "交货期：合同签订后 60 日历天内交付。质保期：验收合格后 24 个月。"
                             "所投产品均为国产产品，不含进口产品。投标文件已通过黑龙江省政府采购网供应商客户端编制，"
                             "加盖电子印章，并使用 CA 证书完成加密。"),
        ],
    },
    "投标人乙": {
        "报价": 950,
        "pages": [
            ("投标函", "投标人：杭州通用机电有限公司（统一社会信用代码 91330100MA2YYYYYY）。"
                       "投标总报价：人民币玖佰伍拾万元整（950.00 万元）。"
                       "投标有效期：自提交投标文件截止之日起 60 日。"
                       "投标保证金：人民币 20 万元，因银行处理原因于投标截止时间次日到账。"),
            ("资格证明", "1. 我方持有有效营业执照，具有独立承担民事责任的能力。"
                         "2. 经查询“信用中国”网站，我方于 2025 年因买卖合同纠纷被列入失信被执行人名单（查询截图见附件，"
                         "该记录尚未修复）。"),
            ("商务技术响应", "交货期 70 日历天，质保期 12 个月。所投产品为国产产品。"
                             "投标文件已加盖电子印章并使用 CA 证书加密。"),
        ],
    },
    "投标人丙": {
        "报价": 1050,
        "pages": [
            ("投标函", "投标人：绍兴重工设备有限公司（统一社会信用代码 91330500MA2ZZZZZZ）。"
                       "投标总报价：人民币壹仟零伍拾万元整（1050.00 万元）。"
                       "投标有效期：自提交投标文件截止之日起 90 日。"
                       "投标保证金：人民币 20 万元，已于投标截止时间前到账。"),
            ("资格证明", "我方持有有效营业执照，具有独立承担民事责任的能力，符合政府采购法第二十二条规定。"
                         "（未在本投标文件中附信用查询结果。）"),
            ("商务技术响应", "交货期 55 日历天，质保期 24 个月。"
                             "本项目所投核心部件采用德国原装进口产品，其余为国产。"
                             "投标文件已加盖电子印章并使用 CA 证书加密。"),
        ],
    },
}

# 报价清单明细（清标用）：甲合规、乙含算术错误、丙超预算且与乙同单价
PRICE_ROWS = {
    "投标人甲": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 500, 8000, 4000000],
        [2, "托辊", "个", 1000, 1500, 1500000],
        [3, "滚筒", "个", 200, 12000, 2400000],
        [4, "轴承", "套", 2000, 950, 1900000],
        ["合计", "", "", "", "", 9800000],
    ],
    "投标人乙": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 500, 7800, 3900000],
        [2, "托辊", "个", 1000, 1500, 1500000],
        [3, "滚筒", "个", 200, 11500, 2300000],
        [4, "轴承", "套", 2000, 920, 1800000],  # 2000×920=1,840,000 ≠ 1,800,000 → 算术错误
        ["合计", "", "", "", "", 9500000],
    ],
    "投标人丙": [
        ["序号", "项目名称", "单位", "数量", "单价", "合价"],
        [1, "输煤皮带", "米", 500, 8500, 4250000],
        [2, "托辊", "个", 1000, 1500, 1500000],  # 与甲/乙“托辊”单价完全相同
        [3, "滚筒", "个", 200, 13500, 2700000],
        [4, "轴承", "套", 2000, 1025, 2050000],
        ["合计", "", "", "", "", 10500000],
    ],
}


def call(method, path, token=None, body=None, raw=None, headers_extra=None, timeout=300):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers_extra:
        h.update(headers_extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else raw
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
            return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "detail": e.read().decode("utf-8", "replace")[:300]}
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2 * (attempt + 1))
    return {"_status": 0, "detail": "网络重试耗尽"}


def make_pdf(pages) -> bytes:
    import fitz

    doc = fitz.open()
    for title, body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), title, fontname="china-s", fontsize=16)
        page.insert_textbox(fitz.Rect(72, 100, 520, 760), body, fontname="china-s", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def make_xlsx(rows) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "报价清单"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(token, filename, blob, doc_role, bidder_id, ctype="application/octet-stream"):
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"doc_role\"\r\n\r\n{doc_role}\r\n".encode()]
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"bidder_id\"\r\n\r\n{bidder_id}\r\n".encode())
    parts.append(
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode() + blob + b"\r\n"
    )
    parts.append(f"--{b}--\r\n".encode())
    return call("POST", f"/projects/{PROJECT_ID}/documents", token, raw=b"".join(parts),
                headers_extra={"Content-Type": f"multipart/form-data; boundary={b}"})


def wait(token, job_id, label, timeout=1200):
    last = None
    for _ in range(timeout // 3):
        time.sleep(3)
        job = call("GET", f"/jobs/{job_id}", token)
        msg = f"{job.get('status')} {job.get('progress')}% {job.get('message','')}"
        if msg != last:
            print(f"   [{label}] {msg}", flush=True)
            last = msg
        if job.get("status") in ("succeeded", "failed"):
            if job["status"] == "failed":
                print(f"   [{label}] 失败: {(job.get('error') or '')[-300:]}", flush=True)
            return job
    return {"status": "timeout"}


def main():
    token = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["token"]

    # 0) 设定预算（示范文本未填）并置为评审中
    call("PATCH", f"/projects/{PROJECT_ID}", token, {"control_price": BUDGET, "status": "reviewing"})
    print(f"项目 #{PROJECT_ID} 预算设为 {BUDGET} 万元")

    # 1) 投标人 + 投标文件 + 报价清单
    existing = {b["name"]: b["id"] for b in call("GET", f"/projects/{PROJECT_ID}/bidders", token)["bidders"]}
    for name, spec in BIDS.items():
        if name not in existing:
            existing[name] = call("POST", f"/projects/{PROJECT_ID}/bidders", token, {"name": name})["id"]
        bid = existing[name]
        r = upload(token, f"{name}_投标文件.pdf", make_pdf(spec["pages"]), "bid", bid, "application/pdf")
        print(f"上传 {name} 投标文件: doc={r.get('id')} {r.get('detail','')}")
        wait(token, r["job_id"], f"解析-{name}")
        p = upload(token, f"{name}_报价清单.xlsx", make_xlsx(PRICE_ROWS[name]), "price", bid)
        wait(token, p["job_id"], f"解析报价-{name}")
    print("投标人:", existing)

    # 2) 确认全部条目（人工在环：确认后才作为评审依据）
    items = call("GET", f"/projects/{PROJECT_ID}/requirement-items", token)["items"]
    todo = [i for i in items if not i["expert_confirmed"]]
    for it in todo:
        call("PATCH", f"/projects/{PROJECT_ID}/requirement-items/{it['id']}", token, {"expert_confirmed": True})
    print(f"确认条目 {len(todo)} 条（共 {len(items)} 条）")

    if "--no-review" in sys.argv:
        print("（--no-review：跳过审查）")
        return

    # 3) 审查（91 条 × 3 家，耗时可观）
    t0 = time.time()
    run = call("POST", f"/projects/{PROJECT_ID}/review/run", token, {})
    print("审查任务:", [(j["bidder_name"], j["job_id"]) for j in run.get("jobs", [])])
    for j in run.get("jobs", []):
        job = wait(token, j["job_id"], f"审查-{j['bidder_name']}")
        print(f"   {j['bidder_name']} 结果: {json.dumps(job.get('result'), ensure_ascii=False)}")
    print(f"审查总耗时 {int(time.time()-t0)} 秒")

    # 4) 清标 + 围标串标预警
    print("清标:", call("POST", f"/projects/{PROJECT_ID}/clearing/run", token))
    print("围标预警:", call("POST", f"/projects/{PROJECT_ID}/collusion/run", token))

    # 5) 汇总
    print("\n===== 审查结论分布 =====")
    for name, bid in existing.items():
        m = call("GET", f"/projects/{PROJECT_ID}/review/matrix?bidder_id={bid}", token)
        print(f"  {name}: {json.dumps(m['summary'], ensure_ascii=False)}")
    alerts = call("GET", f"/projects/{PROJECT_ID}/alerts", token)["alerts"]
    print(f"\n===== 清标与预警 {len(alerts)} 条 =====")
    for a in alerts:
        print(f"  [{a['level']:6s}] {a['title']}")


if __name__ == "__main__":
    main()
