"""项目级权限依赖（统一实现，避免各路由各写一套）。"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ROLE_ADMIN
from ..models import Project, ProjectMember, User


async def require_project(db: AsyncSession, project_id: int) -> Project:
    p = await db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return p


async def require_project_member(db: AsyncSession, project_id: int, user: User) -> ProjectMember | None:
    """admin 全通（返回 None）；其余必须是项目成员。"""
    if user.role == ROLE_ADMIN:
        return None
    row = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )
    member = row.scalar_one_or_none()
    if member is None:
        raise HTTPException(403, "你不是该项目成员")
    return member


async def require_project_manager(db: AsyncSession, project_id: int, user: User) -> ProjectMember | None:
    """项目管理者：admin，或项目内的组长（member_role=leader）。"""
    if user.role == ROLE_ADMIN:
        return None
    row = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.member_role == "leader",
        )
    )
    member = row.scalar_one_or_none()
    if member is None:
        raise HTTPException(403, "仅项目组长可执行此操作")
    return member
