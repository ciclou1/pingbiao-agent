"""应用配置：全部来自环境变量 / .env 文件。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据库（compose 内部主机名 db）
    database_url: str = "postgresql+asyncpg://pingbiao:pingbiao_pass@db:5432/pingbiao"

    # 安全
    secret_key: str = "dev-secret-change-me"
    access_token_expire_hours: float = 12.0

    # 初始管理员（仅在 users 表为空时创建）
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # 文件存储
    files_dir: str = "./data/files"
    max_upload_mb: int = 500

    # 后台任务
    worker_concurrency: int = 2
    # 本地开发可内嵌 worker；生产用独立 worker 容器（避免长任务抢 API 事件循环）
    embedded_worker: bool = False

    # OCR：默认关闭（扫描件标记为「需人工」）；启用需同时给出服务地址
    ocr_enabled: bool = False
    ocr_service_url: str = ""
    ocr_concurrency: int = 2

    # 向量检索（BGE-M3 + pgvector）：留空即纯关键词检索
    embed_service_url: str = ""
    # RRF 融合参数：向量排名与关键词排名各占一票，k 越大越平缓
    rrf_k: int = 60
    vector_candidate_limit: int = 50

    # LLM 网关（M1 起使用；key 不入库不入 git）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_model_fallback: str = ""


settings = Settings()
