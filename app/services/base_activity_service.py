
from sqlalchemy.orm import Session
from app.models.user import User

def pull_full_activities(connect_id:int, db: Session, user: User, incremental: bool = False) -> dict:
  return None

def download_activity(id:int,db: Session, user: User):
  return None

def upload_activity_to_target(id:int,target_platform:str,db: Session, user: User):
  return None
