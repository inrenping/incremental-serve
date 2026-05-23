
from sqlalchemy.orm import Session
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import coros_service,garmin_service
from fastapi import HTTPException

def get_connect_config(db: Session, current_user: User):
  connect_configs = db.query(BaseConnect).filter(BaseConnect.user_id == current_user.user_id).all()
  return connect_configs;

def perform_login(email: str, password: str, platform: str, db: Session, current_user: User)->BaseConnect:
  if platform == "coros":
    coros_auth = coros_service.perform_coros_login(
        db=db,
        user=current_user,
        account=email,
        encrypted_password=password
    )    
    return coros_auth
    
  elif platform == "garmin":
    # 先刷新 secret_string
    updated_auth = garmin_service.get_garmin_secret_string(email,password,platform,db, current_user)
    updated_auth = garmin_service.refresh_garmin_secret_string(updated_auth.id,db, current_user)
    return updated_auth
  else:
    return None;


def perform_relogin(connect_id: int, db: Session, current_user: User)-> BaseConnect:
  """刷新 Token 的操作"""
  if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}  
  base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.user_id,BaseConnect.id == connect_id)
        .first()
        )
  if not base_connect:
        return {"status": "error", "message": "未找到授权配置，请先登录获取授权。"}
  # 如果是高驰，判断token有效性，如果无效的话，调用登录   
  elif base_connect.source_type == "coros": 
    if coros_service.test_coros_token(base_connect.id,db, current_user):
      return base_connect;
    else:
      base_connect = coros_service.perform_coros_login(
        db=db,
        user=current_user,
        account=base_connect.account,
        encrypted_password=base_connect.encrypted_password
        )    
      return base_connect
  # 如果是佳明，先判断 token 有效性，如果无效的话，获取 secret_string 刷新登录，如果刷新不成功，则用邮箱密码重新登录。
  elif base_connect.source_type == "garmin":
     if garmin_service.test_garmin_token(base_connect.id,db, current_user):
      return base_connect;
     else:
      try:
        # 通过 secret_string 来刷新认证
        base_connect = garmin_service.refresh_garmin_secret_string(base_connect.id,db, current_user)
        return base_connect
      except HTTPException:
        base_connect = garmin_service.refresh_garmin_access_token(base_connect.id,db, current_user)
        return base_connect     
  return None;