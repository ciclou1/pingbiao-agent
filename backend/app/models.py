"""ORM 模型（M0 骨架，表名与 PRD 5.5 对齐）。"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    # admin / leader(评标组长) / expert(评标专家) / observer(监督观察)
    role: Mapped[str] = mapped_column(String(16), default="expert", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # goods(货物) / engineering(工程) / service(服务)
    type: Mapped[str] = mapped_column(String(16), default="goods")
    # comprehensive(综合评分法) / lowest_price(最低价法)
    method_type: Mapped[str] = mapped_column(String(24), default="comprehensive")
    control_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # draft(编制中) / reviewing(评审中) / closed(已结束)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectMember(Base):
    """项目成员（专家/组长/观察员），支持 50 人分组评审。"""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # leader / expert / observer
    member_role: Mapped[str] = mapped_column(String(16), default="expert")
    group_no: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Bidder(Base):
    __tablename__ = "bidders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    package_no: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    bidder_id: Mapped[int | None] = mapped_column(ForeignKey("bidders.id"), nullable=True)
    # tender(招标文件) / bid(投标文件) / price(报价清单) / other(其他)
    doc_role: Mapped[str] = mapped_column(String(16), default="other")
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending / parsing / parsed / failed / manual_required
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """后台任务表：解析/评审管线统一走这里，worker 轮询消费。"""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # pending / running / succeeded / failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """审计日志：append-only，评标过程档案的一部分。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LlmCall(Base):
    """LLM 调用留痕：token 用量与耗时，成本可算（PRD 5.4）。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)  # tender_extract / review / qa ...
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RequirementItem(Base):
    """评审条目：从招标文件抽取（或手工录入）的资格/符合性/评分条目（PRD 3.2）。"""

    __tablename__ = "requirement_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # qualification(资格条件) / compliance(符合性审查·否决项) / scoring(评分项)
    item_type: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(32), default="")  # 资质/业绩/财务/人员/保证金/报价...
    clause_text: Mapped[str] = mapped_column(Text)  # 条款原文
    requirement: Mapped[str] = mapped_column(Text, default="")  # 要求要点
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)  # 评分项分值
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    ai_extracted: Mapped[bool] = mapped_column(Boolean, default=True)
    # 人工在环：经专家/组长确认后才成为评审依据
    expert_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocChunk(Base):
    """文档分块：解析后切片入库，供证据检索（M3 起可加 embedding 列）。"""

    __tablename__ = "doc_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    bidder_id: Mapped[int | None] = mapped_column(ForeignKey("bidders.id"), nullable=True, index=True)
    doc_role: Mapped[str] = mapped_column(String(16), default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    chunk_no: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewResult(Base):
    """审查结论：条目 × 投标人 的三态判定 + 证据 + 专家终值（PRD 3.3）。"""

    __tablename__ = "review_results"
    __table_args__ = (UniqueConstraint("item_id", "bidder_id", name="uq_review_item_bidder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("requirement_items.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"), index=True)
    # AI 判定：passed / failed / review
    ai_state: Mapped[str] = mapped_column(String(16), default="review")
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    ai_evidence: Mapped[list] = mapped_column(JSONB, default=list)  # [{page, snippet, score}]
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_check: Mapped[dict] = mapped_column(JSONB, default=dict)  # 确定性校验器结论
    ai_model: Mapped[str] = mapped_column(String(64), default="")
    # 专家终值（评标委员会结论，不隔离）
    expert_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expert_reason: Mapped[str] = mapped_column(Text, default="")
    expert_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expert_decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScoreSuggestion(Base):
    """评分项 AI 建议：要点摘要 + 参考区间（不落最终分）。"""

    __tablename__ = "score_suggestions"
    __table_args__ = (UniqueConstraint("item_id", "bidder_id", name="uq_suggest_item_bidder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("requirement_items.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"), index=True)
    suggested_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    range_low: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    range_high: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Score(Base):
    """专家打分：专家间汇总前互相不可见（PRD 3.4 隔离）。"""

    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("item_id", "bidder_id", "expert_id", name="uq_score_item_bidder_expert"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("requirement_items.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"), index=True)
    expert_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScoreHistory(Base):
    """改分留痕：前后值 + 理由 + 时间。"""

    __tablename__ = "score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("scores.id"), index=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    bidder_id: Mapped[int] = mapped_column(Integer, index=True)
    expert_id: Mapped[int] = mapped_column(Integer, index=True)
    old_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    new_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class QaMessage(Base):
    """专家问答留痕：问题、回答与引文（评标过程档案的一部分）。"""

    __tablename__ = "qa_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    bidder_id: Mapped[int | None] = mapped_column(ForeignKey("bidders.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="all")  # all / tender / bidder
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    insufficient: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PriceItem(Base):
    """报价清单明细行（清标用）。"""

    __tablename__ = "price_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    sheet_name: Mapped[str] = mapped_column(String(120), default="")
    row_no: Mapped[int] = mapped_column(Integer, default=0)
    item_name: Mapped[str] = mapped_column(String(300), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    """清标异常与围标串标预警（供专家参考，不进入否决结论）。"""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # clearing / collusion
    code: Mapped[str] = mapped_column(String(40), default="")  # arithmetic / over_limit / unbalanced / missing_item / meta / similarity / price_pattern
    level: Mapped[str] = mapped_column(String(8), default="medium")  # high / medium / low
    title: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    bidders: Mapped[list] = mapped_column(JSONB, default=list)  # [{id, name}]
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
