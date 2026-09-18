"""项目、成员、投标人、文档上传、任务进度路由（M0 核心业务面）。"""
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import (
    DOC_VIEW_TTL_MINUTES,
    ROLE_ADMIN,
    ROLE_EXPERT,
    ROLE_LEADER,
    _bearer,
    create_doc_view_token,
    decode_doc_view_token,
    get_current_user,
    require_roles,
)
from ..config import settings
from ..database import get_db
from ..models import Bidder, Document, Job, Project, ProjectMember, User
from ..storage import save_upload
from ..worker import enqueue

router = APIRouter(prefix="/projects", tags=["projects"])
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])

PROJECT_TYPES = ("goods", "engineering", "service")
METHOD_TYPES = ("comprehensive", "lowest_price")


class ProjectIn(BaseModel):
    name: str
    type: str = "goods"
    method_type: str = "comprehensive"
    control_price: float | None = None
    description: str = ""


class ProjectUpdateIn(BaseModel):
    name: str | None = None
    type: str | None = None
    method_type: str | None = None
    control_price: float | None = None
    description: str | None = None
    status: str | None = None


class MemberIn(BaseModel):
    user_id: int
    member_role: str = "expert"
    group_no: str = ""


class BidderIn(BaseModel):
    name: str
    package_no: str = ""


def _project_out(p: Project, member_count: int = 0, bidder_count: int = 0, doc_count: int = 0) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type,
        "method_type": p.method_type,
        "control_price": float(p.control_price) if p.control_price is not None else None,
        "status": p.status,
        "description": p.description,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "member_count": member_count,
        "bidder_count": bidder_count,
        "doc_count": doc_count,
    }


async def _counts(db: AsyncSession, project_ids: list[int]) -> dict[int, dict]:
    if not project_ids:
        return {}
    out: dict[int, dict] = {pid: {"member_count": 0, "bidder_count": 0, "doc_count": 0} for pid in project_ids}
    for model, key in ((ProjectMember, "member_count"), (Bidder, "bidder_count"), (Document, "doc_count")):
        rows = await db.execute(
            select(model.project_id, func.count()).where(model.project_id.in_(project_ids)).group_by(model.project_id)
        )
        for pid, n in rows:
            out[pid][key] = n
    return out


async def _require_member(db: AsyncSession, project_id: int, user: User) -> ProjectMember:
    """项目内权限：admin 全通；其余必须是项目成员。"""
    if user.role == ROLE_ADMIN:
        return ProjectMember(project_id=project_id, user_id=user.id, member_role="leader")
    row = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )
    member = row.scalar_one_or_none()
    if member is None:
        raise HTTPException(403, "你不是该项目成员")
    return member


@router.get("")
async def list_projects(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role == ROLE_ADMIN:
        rows = await db.execute(select(Project).order_by(Project.id.desc()))
        projects = list(rows.scalars())
    else:
        rows = await db.execute(
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user.id)
            .order_by(Project.id.desc())
        )
        projects = list(rows.scalars())
    counts = await _counts(db, [p.id for p in projects])
    return {"projects": [_project_out(p, **counts[p.id]) for p in projects]}


@router.post("")
async def create_project(
    body: ProjectIn,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    if body.type not in PROJECT_TYPES:
        raise HTTPException(400, f"type 必须是 {'/'.join(PROJECT_TYPES)}")
    if body.method_type not in METHOD_TYPES:
        raise HTTPException(400, f"method_type 必须是 {'/'.join(METHOD_TYPES)}")
    if not body.name.strip():
        raise HTTPException(400, "项目名称不能为空")
    p = Project(
        name=body.name.strip(),
        type=body.type,
        method_type=body.method_type,
        control_price=body.control_price,
        description=body.description,
        created_by=user.id,
    )
    db.add(p)
    await db.flush()
    # 创建人默认进项目
    db.add(ProjectMember(project_id=p.id, user_id=user.id, member_role=ROLE_LEADER if user.role != ROLE_ADMIN else "leader"))
    await audit(db, action="project_create", user_id=user.id, username=user.username, target_type="project", target_id=p.id)
    await db.commit()
    return _project_out(p)


@router.get("/{project_id}")
async def get_project(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "项目不存在")
    await _require_member(db, project_id, user)
    counts = (await _counts(db, [project_id]))[project_id]
    return _project_out(p, **counts)


@router.patch("/{project_id}")
async def update_project(
    project_id: int,
    body: ProjectUpdateIn,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "项目不存在")
    if body.type is not None and body.type not in PROJECT_TYPES:
        raise HTTPException(400, f"type 必须是 {'/'.join(PROJECT_TYPES)}")
    if body.method_type is not None and body.method_type not in METHOD_TYPES:
        raise HTTPException(400, f"method_type 必须是 {'/'.join(METHOD_TYPES)}")
    for field in ("name", "type", "method_type", "control_price", "description", "status"):
        v = getattr(body, field)
        if v is not None:
            setattr(p, field, v)
    await audit(db, action="project_update", user_id=user.id, username=user.username, target_type="project", target_id=p.id)
    await db.commit()
    counts = (await _counts(db, [project_id]))[project_id]
    return _project_out(p, **counts)


@router.get("/{project_id}/members")
async def list_members(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    await _require_member(db, project_id, user)
    rows = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.id)
    )
    return {
        "members": [
            {
                "id": m.id,
                "user_id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "member_role": m.member_role,
                "group_no": m.group_no,
            }
            for m, u in rows
        ]
    }


@router.post("/{project_id}/members")
async def add_member(
    project_id: int,
    body: MemberIn,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    target = await db.get(User, body.user_id)
    if target is None or not target.is_active:
        raise HTTPException(400, "用户不存在或已停用")
    if body.member_role not in ("leader", "expert", "observer"):
        raise HTTPException(400, "member_role 必须是 leader/expert/observer")
    exists = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == body.user_id)
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(400, "该用户已在项目中")
    m = ProjectMember(project_id=project_id, user_id=body.user_id, member_role=body.member_role, group_no=body.group_no)
    db.add(m)
    await audit(db, action="member_add", user_id=user.id, username=user.username, target_type="project", target_id=project_id, detail={"member_user": target.username, "role": body.member_role})
    await db.commit()
    return {"id": m.id, "user_id": body.user_id, "member_role": body.member_role, "group_no": body.group_no}


@router.delete("/{project_id}/members/{member_id}")
async def remove_member(
    project_id: int,
    member_id: int,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    m = await db.get(ProjectMember, member_id)
    if m is None or m.project_id != project_id:
        raise HTTPException(404, "成员不存在")
    await db.delete(m)
    await audit(db, action="member_remove", user_id=user.id, username=user.username, target_type="project", target_id=project_id)
    await db.commit()
    return {"ok": True}


@router.get("/{project_id}/bidders")
async def list_bidders(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    await _require_member(db, project_id, user)
    rows = await db.execute(select(Bidder).where(Bidder.project_id == project_id).order_by(Bidder.id))
    return {"bidders": [{"id": b.id, "name": b.name, "package_no": b.package_no} for b in rows.scalars()]}


@router.post("/{project_id}/bidders")
async def create_bidder(
    project_id: int,
    body: BidderIn,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    if not body.name.strip():
        raise HTTPException(400, "投标人名称不能为空")
    b = Bidder(project_id=project_id, name=body.name.strip(), package_no=body.package_no)
    db.add(b)
    await db.flush()
    await audit(db, action="bidder_create", user_id=user.id, username=user.username, target_type="bidder", target_id=b.id)
    await db.commit()
    return {"id": b.id, "name": b.name, "package_no": b.package_no}


@router.get("/{project_id}/vector-status")
async def vector_status(
    project_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """向量检索状态：是否配置、服务是否在线、本项目分块的向量覆盖率。

    存在的意义：向量不可用时检索会**静默退回关键词**，用户无从察觉；
    把状态显式暴露出来，避免"以为在用语义检索、其实没有"。
    """
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")

    from .. import embedding
    from ..indexer import coverage
    from ..migrations import vector_ready

    cov = await coverage(db, project_id)
    configured = embedding.is_enabled()
    healthy = None
    if configured:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(settings_embed_url())
            healthy = resp.status_code == 200
        except Exception:  # noqa: BLE001
            healthy = False
    return {
        "configured": configured,
        "service_healthy": healthy,
        "pgvector_ready": await vector_ready(),
        "coverage": cov,
        "active": bool(configured and healthy and (cov.get("embedded") or 0) > 0),
    }


def settings_embed_url() -> str:
    from ..config import settings

    return settings.embed_service_url.rstrip("/") + "/health"


@router.post("/{project_id}/embed-chunks")
async def start_embed_chunks(
    project_id: int,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    """为该项目尚未向量化的分块补建向量索引（存量数据 / 向量服务后启动时用）。"""
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    job = await enqueue(db, "embed_chunks", {"project_id": project_id}, created_by=user.id)
    await audit(
        db,
        action="embed_chunks_start",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"job_id": job.id},
    )
    await db.commit()
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# 文档上传与管理
# ---------------------------------------------------------------------------

DOC_ROLES = ("tender", "bid", "price", "other")


@router.get("/{project_id}/documents")
async def list_documents(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")
    await _require_member(db, project_id, user)
    rows = await db.execute(
        select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())
    )
    return {
        "documents": [
            {
                "id": d.id,
                "bidder_id": d.bidder_id,
                "doc_role": d.doc_role,
                "original_name": d.original_name,
                "sha256": d.sha256,
                "size_bytes": d.size_bytes,
                "page_count": d.page_count,
                "parse_status": d.parse_status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows.scalars()
        ]
    }


@router.post("/{project_id}/documents")
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    doc_role: str = Form("other"),
    bidder_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "项目不存在")
    await _require_member(db, project_id, user)
    if doc_role not in DOC_ROLES:
        raise HTTPException(400, f"doc_role 必须是 {'/'.join(DOC_ROLES)}")
    if bidder_id is not None:
        bidder = await db.get(Bidder, bidder_id)
        if bidder is None or bidder.project_id != project_id:
            raise HTTPException(400, "bidder_id 无效")

    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {settings.max_upload_mb}MB 限制")
    if not data:
        raise HTTPException(400, "空文件")

    filename = file.filename or "unnamed"
    ext = Path(filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".xlsx", ".zip"):
        raise HTTPException(400, "仅支持 pdf/docx/xlsx/zip 文件")

    sha256, stored_path, size = await save_upload(data, filename)
    doc = Document(
        project_id=project_id,
        bidder_id=bidder_id,
        doc_role=doc_role,
        original_name=Path(filename).name,
        stored_path=stored_path,
        sha256=sha256,
        size_bytes=size,
        mime=file.content_type or "",
        created_by=user.id,
    )
    db.add(doc)
    await db.flush()
    job = await enqueue(db, "parse_document", {"document_id": doc.id}, created_by=user.id)
    await audit(
        db,
        action="document_upload",
        user_id=user.id,
        username=user.username,
        target_type="document",
        target_id=doc.id,
        detail={"sha256": sha256, "doc_role": doc_role, "job_id": job.id},
    )
    await db.commit()
    return {
        "id": doc.id,
        "sha256": sha256,
        "job_id": job.id,
        "parse_status": doc.parse_status,
        "duplicate": True,
    }


@router.delete("/{project_id}/documents/{doc_id}")
async def delete_document(
    project_id: int,
    doc_id: int,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_LEADER)),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if doc is None or doc.project_id != project_id:
        raise HTTPException(404, "文档不存在")
    # 内容寻址：确认没有其他 Document 引用同一物理文件才删除
    refs = await db.execute(
        select(func.count()).where(Document.stored_path == doc.stored_path, Document.id != doc.id)
    )
    if (refs.scalar_one() or 0) == 0:
        Path(doc.stored_path).unlink(missing_ok=True)
    await db.delete(doc)
    await audit(db, action="document_delete", user_id=user.id, username=user.username, target_type="document", target_id=doc_id)
    await db.commit()
    return {"ok": True}


@jobs_router.get("/{job_id}")
async def get_job(job_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "result": job.result,
        "error": job.error,
    }


# ---------------------------------------------------------------------------
# 文件下载 / 在线预览（预览走短令牌，供 iframe 定位到页）
# ---------------------------------------------------------------------------

files_router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{project_id}/documents/{doc_id}/download")
async def download_document(
    project_id: int,
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if doc is None or doc.project_id != project_id:
        raise HTTPException(404, "文档不存在")
    await _require_member(db, project_id, user)
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(404, "文件已不在存储中")
    return FileResponse(path, filename=doc.original_name)


@router.post("/{project_id}/documents/{doc_id}/view-token")
async def create_view_token(
    project_id: int,
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """签发 10 分钟有效的文件查看短令牌（供浏览器内置 PDF 阅读器 iframe 使用）。"""
    doc = await db.get(Document, doc_id)
    if doc is None or doc.project_id != project_id:
        raise HTTPException(404, "文档不存在")
    await _require_member(db, project_id, user)
    return {"token": create_doc_view_token(doc_id), "expires_in": DOC_VIEW_TTL_MINUTES * 60}


@files_router.get("/{doc_id}/file")
async def view_document_file(
    doc_id: int,
    vt: str | None = Query(None, description="查看短令牌（iframe 无法带 Authorization 头时使用）"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    """内联返回文件（浏览器内置阅读器可直接 #page=N 定位）。"""
    doc = await db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "文档不存在")
    if vt:
        if not decode_doc_view_token(vt, doc_id):
            raise HTTPException(401, "查看令牌无效或已过期")
    else:
        if creds is None:
            raise HTTPException(401, "未登录")
        try:
            payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, "登录已失效")
        viewer = await db.get(User, int(payload["sub"]))
        if viewer is None or not viewer.is_active:
            raise HTTPException(401, "账号不可用")
        await _require_member(db, doc.project_id, viewer)
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(404, "文件已不在存储中")
    return FileResponse(path, media_type=doc.mime or "application/octet-stream", content_disposition_type="inline")


_ = (ROLE_EXPERT,)
