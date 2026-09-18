"""新建评测样例脚手架：建目录 + 写 case.json 模板，你只需把标书文件放进去。

用法：python scripts/new_case.py 平海电厂输煤备件
然后：
  eval/cases/平海电厂输煤备件/
    ├── case.json        ← 填 control_price、文件名；标准答案可留空（走冒烟模式）
    ├── tender.pdf       ← 放真实招标文件（文件名与 case.json 里一致）
    └── 投标人甲.pdf ...  ← 放各家投标文件
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 2:
        print("用法：python scripts/new_case.py <样例名>")
        sys.exit(1)
    name = sys.argv[1]
    d = ROOT / "eval" / "cases" / name
    d.mkdir(parents=True, exist_ok=True)
    case_file = d / "case.json"
    if case_file.exists():
        print(f"已存在：{case_file}（未覆盖）")
        return
    case = {
        "name": name,
        "note": "真实项目：把招标文件与各家投标文件放在本目录，文件名与下方一致；标准答案可留空走冒烟模式",
        "control_price": None,
        "tender": "tender.pdf",
        "bids": {"投标人甲": ["投标人甲.pdf"]},
        "expected_items": [],
        "expected_reviews": [],
        "expected_evidence": [],
    }
    case_file.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已创建 {d}")
    print("  1) 把真实招标文件存为 tender.pdf、投标文件存为 投标人甲.pdf（或改 case.json 里的文件名）")
    print("  2) 多家投标人：在 case.json 的 bids 里加键值对")
    print(f"  3) 运行：python scripts/eval.py {name}")


if __name__ == "__main__":
    main()
