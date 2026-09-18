"""生成评测样例：合成招标文件 + 3 家投标文件 + 标准答案（ground truth）。

在 api 容器内运行（需要 PyMuPDF / python-docx）：
  docker compose -p pingbiao cp eval/make_demo_case.py api:/tmp/
  docker compose -p pingbiao exec -T api python /tmp/make_demo_case.py /eval/cases/demo

产出：tender.pdf、投标人{甲,乙,丙}.pdf、case.json（标准答案，随文件一起生成，避免两者漂移）。
真实招标项目请直接放文件 + 手写 case.json。
"""
import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/eval/cases/demo"

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

# 标准答案：条目（类型 + 特征串 + 期望页码）与审查判定（投标人 + 条目特征串 + 期望结论）
CASE = {
    "name": "平海电厂输煤备件（合成样例）",
    "note": "合成样例用于回归；真实项目请按同结构放文件与 case.json",
    "control_price": 120,
    "tender": "tender.pdf",
    "bids": {k: [f"{k}.pdf"] for k in BIDS},
    "expected_items": [
        {"type": "qualification", "clause_contains": "独立法人资格", "page": 2},
        {"type": "qualification", "clause_contains": "至少2个", "page": 2},
        {"type": "qualification", "clause_contains": "财务状况良好", "page": 2},
        {"type": "qualification", "clause_contains": "项目负责人", "page": 2},
        {"type": "qualification", "clause_contains": "ISO9001", "page": 2},
        {"type": "compliance", "clause_contains": "签字盖章", "page": 4},
        {"type": "compliance", "clause_contains": "最高限价118万元", "page": 4},
        {"type": "compliance", "clause_contains": "投标有效期不足90日", "page": 4},
        {"type": "compliance", "clause_contains": "投标保证金未按时", "page": 4},
        {"type": "compliance", "clause_contains": "失信被执行人", "page": 4},
        {"type": "scoring", "clause_contains": "投标报价（40分）", "page": 5},
        {"type": "scoring", "clause_contains": "类似业绩（20分）", "page": 5},
        {"type": "scoring", "clause_contains": "技术方案（25分）", "page": 5},
        {"type": "scoring", "clause_contains": "企业资质信誉（15分）", "page": 5},
    ],
    # 判定标准答案：只列结论明确、可由材料直接判断的条目
    "expected_reviews": [
        {"bidder": "投标人甲", "clause_contains": "最高限价118万元", "state": "passed"},
        {"bidder": "投标人甲", "clause_contains": "至少2个", "state": "passed"},
        {"bidder": "投标人甲", "clause_contains": "投标有效期不足90日", "state": "passed"},
        {"bidder": "投标人甲", "clause_contains": "独立法人资格", "state": "passed"},
        {"bidder": "投标人乙", "clause_contains": "至少2个", "state": "failed"},
        {"bidder": "投标人乙", "clause_contains": "投标有效期不足90日", "state": "failed"},
        {"bidder": "投标人乙", "clause_contains": "最高限价118万元", "state": "passed"},
        {"bidder": "投标人丙", "clause_contains": "最高限价118万元", "state": "failed"},
        {"bidder": "投标人丙", "clause_contains": "投标有效期不足90日", "state": "passed"},
    ],
    # 引文基准：这些条目的证据应落在招标文件对应页附近
    "expected_evidence": [
        {"bidder": "投标人乙", "clause_contains": "投标有效期不足90日", "page": 1},
    ],
}


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


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tender.pdf"), "wb") as f:
        f.write(make_pdf(TENDER))
    for name, pages in BIDS.items():
        with open(os.path.join(OUT, f"{name}.pdf"), "wb") as f:
            f.write(make_pdf(pages))
    with open(os.path.join(OUT, "case.json"), "w", encoding="utf-8") as f:
        json.dump(CASE, f, ensure_ascii=False, indent=2)
    print(f"已生成评测样例到 {OUT}：tender.pdf + {len(BIDS)} 份投标文件 + case.json")
    print(f"  条目标准答案 {len(CASE['expected_items'])} 条，判定标准答案 {len(CASE['expected_reviews'])} 条")


if __name__ == "__main__":
    main()
