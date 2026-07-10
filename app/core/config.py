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
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment variables")

    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    if ENV == "production" and not GOOGLE_CLIENT_ID:
        # 生产环境下如果缺失关键变量，提前抛出异常防止服务带着错误配置运行
        raise ValueError("GOOGLE_CLIENT_ID must be set in production")
    GOOGLE_ACCOUNT_SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if ENV == "production" and not GOOGLE_ACCOUNT_SERVICE_JSON:
        google_account_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
        if not google_account_b64:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_B64 must be set in production "
                "when GOOGLE_SERVICE_ACCOUNT_FILE is not set"
            )
        GOOGLE_ACCOUNT_SERVICE_JSON = base64.b64decode(
            google_account_b64
        ).decode("utf-8")

    # SCHEMA = os.getenv("SCHEMA")
    SECRET_KEY = os.getenv("SECRET_KEY")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    RESEND_EMAIL_FROM = os.getenv("RESEND_EMAIL_FROM")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    # GITHUB 不允许 GITHUB_ 开头的变量，改成 GIT_HUB_ 开头
    GIT_HUB_CLIENT_ID = os.getenv("GIT_HUB_CLIENT_ID")
    GIT_HUB_CLIENT_SECRET = os.getenv("GIT_HUB_CLIENT_SECRET")


settings = Settings()
