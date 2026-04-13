import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL")
    SCHEMA = os.getenv("SCHEMA")
    SECRET_KEY = os.getenv("SECRET_KEY")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")

settings = Settings()