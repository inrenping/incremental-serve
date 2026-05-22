
from curl_cffi import Session
from app.models.user import User

def get_connect_config(db: Session, user: User):
  return None

def perform_login(email: str, password: str, platform: str, db: Session, user: User):
  return None