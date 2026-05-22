
from curl_cffi import Session
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import coros_service,garmin_service


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
    base_connect = coros_service.perform_coros_login(
       db=db,
       user=current_user,
       account=base_connect.account,
       encrypted_password=base_connect.encrypted_password,
       is_refresh=True
      )    
    return base_connect


  # 如果是佳明，先判断 token 有效性，如果无效的话，获取 secret_string 刷新登录，如果刷新不成功，则用邮箱密码重新登录。
  elif base_connect.source_type == "garmin":
     return None;
  return None;