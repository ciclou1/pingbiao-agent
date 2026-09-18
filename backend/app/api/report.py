"""评标报告导出路由（PRD 3.7）。导出动作留痕。"""
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import get_current_user
from ..database import get_db
from ..models import User
from ..report import build_docx, build_xlsx, collect_report_data
from .deps import require_project, require_project_manager

router = APIRouter(prefix="/projects/{project_id}/report", tags=["report"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _disposition(filename: str) -> dict:
    return {"Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}"}


@router.get("/data")
async def report_data(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """报告预览数据（含评分汇总 → 仅组长/admin）。"""
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    try:
        return await collect_report_data(db, project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/export.docx")
async def export_docx(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    data = await collect_report_data(db, project_id)
    payload = build_docx(data)
    await audit(
        db,
        action="report_export",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"format": "docx", "bytes": len(payload)},
    )
    await db.commit()
    name = f"评标报告_{data['project']['name']}.docx"
    return StreamingResponse(io.BytesIO(payload), media_type=DOCX_MIME, headers=_disposition(name))


@router.get("/export.xlsx")
async def export_xlsx(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project(db, project_id)
    await require_project_manager(db, project_id, user)
    data = await collect_report_data(db, project_id)
    payload = build_xlsx(data)
    await audit(
        db,
        action="report_export",
        user_id=user.id,
        username=user.username,
        target_type="project",
        target_id=project_id,
        detail={"format": "xlsx", "bytes": len(payload)},
    )
    await db.commit()
    name = f"评标明细_{data['project']['name']}.xlsx"
    return StreamingResponse(io.BytesIO(payload), media_type=XLSX_MIME, headers=_disposition(name))
