"""检索能力探针：判断当前关键词检索（字符 n-gram TF-IDF）够不够，还是需要向量检索。

做法：对最近一个评测项目的招标文件分块，用两组查询测 recall@K——
  A 组：与文档同源的措辞（条目原文，系统内部调用场景）
  B 组：专家口语化改写（同义不同词，例如「履约担保」vs「投标保证金」）
B 组掉得多 → 说明纯关键词检索撑不住自然语言提问，需要 embedding；
若加一层同义词扩展就能补齐，则可以先不上模型（成本差 2GB + 一个推理服务）。

在 api 容器内运行：docker compose -p pingbiao cp scripts/retrieval_probe.py api:/tmp/ && docker compose -p pingbiao exec -T api python /tmp/retrieval_probe.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import DocChunk, Document, Project  # noqa: E402
from app.retriever import build_index, retrieve  # noqa: E402

TOP_K = 6

# (查询, 期望页码, 类型)
PROBES = [
    # A 组：同源措辞（等于系统用条目原文检索）
    ("投标人须具有独立法人资格，持有效营业执照", 2, "A 同源"),
    ("具有近三年（2023年至今）至少2个火电厂输煤系统备件供货业绩", 2, "A 同源"),
    ("投标报价超过最高限价118万元的，否决其投标", 4, "A 同源"),
    ("通过ISO9001质量管理体系认证", 2, "A 同源"),
    ("投标文件应包含：商务标、技术标、报价表", 6, "A 同源"),
    # B 组：专家口语化改写（同义不同词）
    ("公司准入要什么门槛", 2, "B 改写"),
    ("以前做过哪些类似项目有要求吗", 2, "B 改写"),
    ("最高能报多少钱", 4, "B 改写"),
    ("履约担保要交多少", 3, "B 改写"),
    ("质量体系认证有要求吗", 2, "B 改写"),
    ("标书要交几份", 6, "B 改写"),
    ("财务上有什么硬性条件", 2, "B 改写"),
    ("项目负责人要有多少年经验", 2, "B 改写"),
]

# 同义词扩展：验证「不上模型也能补一部分」的假设
SYNONYMS = {
    "门槛": ["资格", "要求", "条件", "资质"],
    "担保": ["保证金"],
    "多少钱": ["报价", "限价", "金额"],
    "钱": ["金额", "报价", "保证金"],
    "几份": ["正本", "副本", "份数"],
    "标书": ["投标文件"],
    "质量体系": ["ISO9001", "质量管理体系"],
    "财务": ["审计报告", "资产负债"],
    "负责人": ["项目负责人", "职称", "经验"],
    "经验": ["业绩", "经历"],
    "项目": ["业绩", "合同"],
}

# 困难改写：既无字面重合、同义词表也未覆盖 → 这才是向量检索真正要证明价值的地方
# (查询, 可接受的页码集合, 说明)
# 注意：同一实质要求在招标文件里往往出现在多个章节（如"失信"散落在资格、承诺函、审查表），
# 命中其中任意一处都算对 —— 用单页做期望会低估召回（这是实测踩到的口径问题）。
HARD_PROBES = [
    ("供应商被法院列为老赖还能参加吗", {13, 14, 36, 37, 67, 68, 69}, "老赖 → 失信被执行人"),
    ("投标文件用什么软件制作和盖章", {16, 19, 21}, "工具 → 投标客户端/CA/电子印章"),
    ("有没有要求节能环保的产品", {30, 44, 45}, "节能环保 → 强制采购品目清单"),
    ("厂家出的货必须原装进口吗", {44, 45}, "原装进口 → 进口产品"),
    ("中途能不能换个人来送货", {14, 15, 45}, "换人 → 分包/联合体履约主体"),
    ("钱要压多久才能退回来", {18, 19}, "压钱 → 保证金有效期"),
    ("文件交上去还能改吗", {41}, "改 → 报价修正/澄清"),
    ("别人能代我递材料吗", {24, 32}, "代递 → 委托同一单位办理投标事宜"),
]


def expand(query: str) -> str:
    return query + " " + " ".join(w for k, ws in SYNONYMS.items() if k in query for w in ws)


async def check_self_retrieval(sample: int = 15) -> None:
    """证据定位自洽率：用条目原文去检索，看能否命中它自己的来源页。

    这是审查/问答证据准确性的地基——检索不到条目自身的来源页，
    给专家的证据就是错的。（真实文件尤其重要：条款措辞长且分散在长文档里）
    """
    from sqlalchemy import select as _select

    from app.models import Document, Project, RequirementItem

    async with SessionLocal() as db:
        proj = (
            await db.execute(_select(Project).where(Project.name.like("评测-%")).order_by(Project.id.desc()).limit(1))
        ).scalar_one_or_none()
        if proj is None:
            print("未找到评测项目")
            return
        docs = list(
            (await db.execute(_select(Document).where(Document.project_id == proj.id, Document.doc_role == "tender"))).scalars()
        )
        chunks = list(
            (await db.execute(_select(DocChunk).where(DocChunk.document_id.in_([d.id for d in docs])))).scalars()
        )
        items = list(
            (
                await db.execute(
                    _select(RequirementItem).where(RequirementItem.project_id == proj.id).limit(sample)
                )
            ).scalars()
        )

    index = build_index([{"id": c.id, "page_no": c.page_no, "text": c.text} for c in chunks])
    hit = 0
    total = 0
    print(f"项目：{proj.name}（#{proj.id}）　分块 {len(chunks)}　抽样 {len(items)} 条\n")
    for it in items:
        if not it.source_page or not it.clause_text:
            continue
        total += 1
        query = f"{it.clause_text} {it.requirement or ''}"
        pages = [h["page"] for h in retrieve(index, query, top_k=TOP_K)]
        ok = it.source_page in pages
        hit += 1 if ok else 0
        flag = "✓" if ok else "✗"
        print(f"  {flag} 期望 p{it.source_page:<3} 命中 {str(pages):<24} {(it.clause_text or '')[:40]}")
    print(f"\n证据定位自洽率：{hit}/{total} = {hit / total:.0%}" if total else "无可抽样条目")


async def run_hard_probes(use_vector: bool = True) -> None:
    """困难改写对照：关键词（含同义词扩展） vs 混合检索（关键词+向量）。

    向量服务未启用时只跑关键词一侧，便于先拿到基线。
    """
    from sqlalchemy import select as _select

    from app import embedding
    from app.models import Document, Project
    from app.retriever import hybrid_retrieve
    from app.synonyms import expand

    async with SessionLocal() as db:
        proj = (
            await db.execute(_select(Project).where(Project.name.like("评测-%")).order_by(Project.id.desc()).limit(1))
        ).scalar_one_or_none()
        if proj is None:
            print("未找到评测项目")
            return
        docs = list(
            (await db.execute(_select(Document).where(Document.project_id == proj.id, Document.doc_role == "tender"))).scalars()
        )
        chunks = list(
            (
                await db.execute(
                    _select(DocChunk).where(DocChunk.document_id.in_([d.id for d in docs]))
                )
            ).scalars()
        )
        index = build_index([{"id": c.id, "page_no": c.page_no, "text": c.text} for c in chunks])

        vec_on = embedding.is_enabled()
        print(f"项目：{proj.name}（#{proj.id}）分块 {len(chunks)}　向量服务：{'已配置' if vec_on else '未配置'}\n")
        print(f"{'查询':34s}{'期望页':>7s}{'关键词':>9s}{'混合(+向量)':>12s}  说明")
        base_hit = hyd_hit = 0
        for q, pages_ok, note in HARD_PROBES:
            kw_pages = [h["page"] for h in retrieve(index, expand(q), top_k=TOP_K)]
            kw_ok = bool(set(kw_pages) & pages_ok)
            base_hit += kw_ok
            hyd_ok = None
            if vec_on:
                hits = await hybrid_retrieve(
                    db, index, q, project_id=proj.id, scope="tender", top_k=TOP_K, expand_fn=expand
                )
                hyd_ok = bool({h["page"] for h in hits} & pages_ok)
                hyd_hit += hyd_ok
            print(f"{q:34s}{str(sorted(pages_ok))[:14]:>15s}{'✓' if kw_ok else '✗':>7s}"
                  f"{(('✓' if hyd_ok else '✗') if hyd_ok is not None else '—'):>12s}  {note}")
        n = len(HARD_PROBES)
        print(f"\nrecall@{TOP_K}：关键词+同义词 {base_hit}/{n} = {base_hit / n:.0%}"
              + (f"　混合检索（+向量） {hyd_hit}/{n} = {hyd_hit / n:.0%}" if vec_on else "　（向量未启用，无对照）"))


async def main():
    async with SessionLocal() as db:
        proj = (
            await db.execute(select(Project).where(Project.name.like("评测-%")).order_by(Project.id.desc()).limit(1))
        ).scalar_one_or_none()
        if proj is None:
            print("未找到评测项目（名字以「评测-」开头），请先跑 scripts/eval.py demo")
            return
        docs = list(
            (await db.execute(select(Document).where(Document.project_id == proj.id, Document.doc_role == "tender"))).scalars()
        )
        chunks = list(
            (
                await db.execute(
                    select(DocChunk).where(DocChunk.document_id.in_([d.id for d in docs]))
                )
            ).scalars()
        )
    print(f"项目：{proj.name}（#{proj.id}），招标文件分块 {len(chunks)} 块，top_k={TOP_K}\n")

    index = build_index([{"id": c.id, "page_no": c.page_no, "text": c.text} for c in chunks])

    rows = []
    for query, page, kind in PROBES:
        hits = retrieve(index, query, top_k=TOP_K)
        pages = [h["page"] for h in hits]
        ok = page in pages
        hits_syn = retrieve(index, expand(query), top_k=TOP_K)
        pages_syn = [h["page"] for h in hits_syn]
        ok_syn = page in pages_syn
        rows.append((kind, query, page, pages, ok, ok_syn))
        print(f"[{kind}] {query}")
        print(f"    期望 p{page} | 关键词命中页 {pages or '（无）'} → {'✓' if ok else '✗'}　"
              f"| 加同义词后 {pages_syn or '（无）'} → {'✓' if ok_syn else '✗'}")

    print("\n=== 汇总 ===")
    for kind in ("A 同源", "B 改写"):
        sub = [r for r in rows if r[0] == kind]
        base = sum(1 for r in sub if r[4]) / len(sub)
        syn = sum(1 for r in sub if r[5]) / len(sub)
        print(f"{kind}：recall@{TOP_K} 纯关键词 {base:.0%} → 加同义词扩展 {syn:.0%}（{len(sub)} 条）")


if __name__ == "__main__":
    if "--hard" in sys.argv:
        asyncio.run(run_hard_probes())
    elif "--items" in sys.argv:
        n = 15
        try:
            n = int(sys.argv[sys.argv.index("--items") + 1])
        except (IndexError, ValueError):
            pass
        asyncio.run(check_self_retrieval(n))
    else:
        asyncio.run(main())
