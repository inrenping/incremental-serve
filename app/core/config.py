import base64
import os
from pathlib import Path
from dotenv import load_dotenv

# 获取项目根目录的绝对路径，确保在任何环境下都能找到 .env
BASE_DIR = Path(__file__).resolve().parent.parent.parent
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)


class Settings:
    ENV = os.getenv("APP_ENV", "development")
    DATABASE_URL = os.getenv("DATABASE_URL")

    # MCP 服务的 resource 标识（RFC 8707 Resource Indicators）。
    # 必须与 incremental-mcp 侧的 app.config.Settings.mcp_resource 完全一致：
    # 该值同时用于 PRM 元数据的 resource 字段、令牌的 aud 声明与 resource 参数校验。
    MCP_RESOURCE_URI = os.getenv("MCP_RESOURCE_URI", "https://incremental.icu/mcp")
    # 站点 canonical 域名。注：PRM 的 resource 不再是该值，请勿混淆。
    CANONICAL_ORIGIN = os.getenv("CANONICAL_ORIGIN", "https://incremental.icu")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment variables")

    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    if ENV == "production" and not GOOGLE_CLIENT_ID:
        # 生产环境下如果缺失关键变量，提前抛出异常防止服务带着错误配置运行
        raise ValueError("GOOGLE_CLIENT_ID must be set in production")
    google_account_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if ENV == "production" and not google_account_b64:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_B64 must be set in production")
    GOOGLE_ACCOUNT_SERVICE_JSON = (
        base64.b64decode(google_account_b64).decode("utf-8")
        if google_account_b64
        else None
    )

    # SCHEMA = os.getenv("SCHEMA")
    SECRET_KEY = os.getenv("SECRET_KEY")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    RESEND_EMAIL_FROM = os.getenv("RESEND_EMAIL_FROM")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    # GITHUB 不允许 GITHUB_ 开头的变量，改成 GIT_HUB_ 开头
    GIT_HUB_CLIENT_ID = os.getenv("GIT_HUB_CLIENT_ID")
    GIT_HUB_CLIENT_SECRET = os.getenv("GIT_HUB_CLIENT_SECRET")

    # Clerk 认证
    CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
    CLERK_ISSUER = os.getenv("CLERK_ISSUER")  # e.g. https://xxx.clerk.accounts.dev
    CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET")


settings = Settings()
