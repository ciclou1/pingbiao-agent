"""审计日志：统一写入口，append-only。"""
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


async def audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    username: str = "",
    target_type: str = "",
    target_id: str | int = "",
    detail: dict | None = None,
    ip: str = "",
) -> None:
    """写一条审计日志。由各路由在业务事务内调用。"""
    db.add(
        AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail or {},
            ip=ip,
        )
    )
