import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL")
    # SCHEMA = os.getenv("SCHEMA")
    SECRET_KEY = os.getenv("SECRET_KEY")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    RESEND_EMAIL_FROM = os.getenv("RESEND_EMAIL_FROM")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GITHUB_CLIENT_ID = os.getenv("GIT_HUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET = os.getenv("GIT_HUB_CLIENT_SECRET")

settings = Settings()