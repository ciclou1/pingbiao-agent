"""FastAPI 主入口：CORS、路由挂载、启动建表 + 初始管理员 + worker。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .auth import ROLE_ADMIN, hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User
from .worker import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def ensure_tables_and_admin() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 已有表的字段/索引/扩展（create_all 管不到）
    from .migrations import run_migrations

    applied = await run_migrations()
    logging.getLogger("startup").info(
        "迁移结果：%s", "、".join(f"{k}{'✓' if v else '✗'}" for k, v in applied.items())
    )
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        if count == 0:
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    display_name="系统管理员",
                    role=ROLE_ADMIN,
                )
            )
            await db.commit()
            logging.getLogger("startup").info("初始管理员已创建: %s", settings.admin_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_tables_and_admin()
    task = None
    if settings.embedded_worker:
        # 仅本地开发：生产环境用独立 worker 容器，避免长任务与请求争抢事件循环
        task = asyncio.create_task(worker_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="辅助评标智能体 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pingbiao-api"}


from .api import audit as audit_api  # noqa: E402
from .api import auth as auth_api  # noqa: E402
from .api import clearing as clearing_api  # noqa: E402
from .api import items as items_api  # noqa: E402
from .api import projects as projects_api  # noqa: E402
from .api import qa as qa_api  # noqa: E402
from .api import report as report_api  # noqa: E402
from .api import review as review_api  # noqa: E402

app.include_router(auth_api.router, prefix="/api/v1")
app.include_router(auth_api.users_router, prefix="/api/v1")
app.include_router(projects_api.router, prefix="/api/v1")
app.include_router(projects_api.jobs_router, prefix="/api/v1")
app.include_router(items_api.router, prefix="/api/v1")
app.include_router(review_api.router, prefix="/api/v1")
app.include_router(qa_api.router, prefix="/api/v1")
app.include_router(report_api.router, prefix="/api/v1")
app.include_router(clearing_api.router, prefix="/api/v1")
app.include_router(projects_api.files_router, prefix="/api/v1")
app.include_router(audit_api.router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logging.getLogger("api").exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
