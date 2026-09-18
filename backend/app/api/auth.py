"""认证路由：登录、当前用户、用户管理（admin）。"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import (
    ROLE_LABELS,
    ROLES,
    ROLE_ADMIN,
    ROLE_EXPERT,
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from ..database import get_db
from ..models import User

_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str
    is_active: bool

    class Config:
        from_attributes = True


class UserCreateIn(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = ROLE_EXPERT


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "")


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "is_active": u.is_active,
    }


@router.post("/login")
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    row = await db.execute(select(User).where(User.username == body.username))
    user = row.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        await audit(
            db,
            action="login_failed",
            username=body.username,
            ip=_client_ip(request),
            detail={"reason": "bad_credentials"},
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    token = create_access_token(user)
    await audit(db, action="login", user_id=user.id, username=user.username, ip=_client_ip(request))
    await db.commit()
    return {"token": token, "user": _user_out(user)}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@users_router.get("")
async def list_users(
    _: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(select(User).order_by(User.id))
    return {"users": [_user_out(u) for u in rows.scalars()]}


@users_router.post("")
async def create_user(
    body: UserCreateIn,
    request: Request,
    current: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"角色必须是 {'/'.join(ROLES)}")
    if len(body.password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "用户名已存在")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
        role=body.role,
    )
    db.add(user)
    await db.flush()
    await audit(
        db,
        action="user_create",
        user_id=current.id,
        username=current.username,
        target_type="user",
        target_id=user.id,
        detail={"role": body.role},
        ip=_client_ip(request),
    )
    await db.commit()
    return _user_out(user)


@users_router.patch("/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdateIn,
    _: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"角色必须是 {'/'.join(ROLES)}")
        user.role = body.role
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码至少 6 位")
        user.password_hash = hash_password(body.password)
    await audit(db, action="user_update", target_type="user", target_id=user.id, detail={"role": user.role})
    await db.commit()
    return _user_out(user)
