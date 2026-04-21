import os
from pathlib import Path
from dotenv import load_dotenv

# 获取项目根目录的绝对路径，确保在任何环境下都能找到 .env
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(os.path.join(BASE_DIR, ".env"))

class Settings:
    ENV = os.getenv("APP_ENV", "development")
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment variables")
        
    # SCHEMA = os.getenv("SCHEMA")
    SECRET_KEY = os.getenv("SECRET_KEY")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    RESEND_EMAIL_FROM = os.getenv("RESEND_EMAIL_FROM")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    # GITHUB 不允许 GITHUB_ 开头的变量，改成 GIT_HUB_ 开头
    GIT_HUB_CLIENT_ID = os.getenv("GIT_HUB_CLIENT_ID")
    GIT_HUB_CLIENT_SECRET = os.getenv("GIT_HUB_CLIENT_SECRET")

settings = Settings()