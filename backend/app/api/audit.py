"""审计日志查询（admin/observer 只读）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ROLE_ADMIN, ROLE_OBSERVER, require_roles
from ..database import get_db
from ..models import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: object = Depends(require_roles(ROLE_ADMIN, ROLE_OBSERVER)),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    rows = await db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "total": total,
        "page": page,
        "logs": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "username": a.username,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "detail": a.detail,
                "ip": a.ip,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows.scalars()
        ],
    }
