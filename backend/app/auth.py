"""鉴权：bcrypt 口令哈希 + PyJWT 令牌 + 角色依赖。"""
import datetime as dt

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)

ROLE_ADMIN = "admin"
ROLE_LEADER = "leader"
ROLE_EXPERT = "expert"
ROLE_OBSERVER = "observer"
ROLES = (ROLE_ADMIN, ROLE_LEADER, ROLE_EXPERT, ROLE_OBSERVER)
ROLE_LABELS = {
    ROLE_ADMIN: "系统管理员",
    ROLE_LEADER: "评标组长",
    ROLE_EXPERT: "评标专家",
    ROLE_OBSERVER: "监督观察",
}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "username": user.username,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=settings.access_token_expire_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不可用")
    return user


def require_roles(*roles: str):
    """角色依赖：require_roles(ROLE_ADMIN) 或 require_roles() 表示任意登录用户。"""

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "权限不足")
        return user

    return _dep


# ---------------------------------------------------------------------------
# 文档查看短令牌：iframe/embed 无法携带 Authorization 头，用 10 分钟有效、
# 且限定 document_id 的短令牌走 query 参数（泄露面有限，仅能看该文件）。
# ---------------------------------------------------------------------------

DOC_VIEW_TTL_MINUTES = 10


def create_doc_view_token(document_id: int) -> str:
    payload = {
        "scope": "doc_view",
        "doc_id": document_id,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=DOC_VIEW_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_doc_view_token(token: str, document_id: int) -> bool:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("scope") == "doc_view" and int(payload.get("doc_id", -1)) == document_id
