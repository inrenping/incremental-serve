

from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends
from pydantic.v1 import BaseModel

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.user import User

from app.services import base_connect_service,base_activity_service

router = APIRouter()

class LoginRequest(BaseModel):
    """登录请求模型"""
    platform: str
    email: str
    password: str


@router.get("/getConnectConfig")
def get_connect_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的连接配置。
    """
    connect_config = base_connect_service.get_connect_config(db, current_user);
    
    return {"status": "success", "data": connect_config}

@router.post("/login")
def login(
    login_request: LoginRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
  ):
    """
    登录并将认证信息存入数据库。
    成功后将保存 accessToken 到对应的连接表中。
    """
    base_connect = base_connect_service.perform_login(login_request.email,login_request.password,login_request.platform,db, current_user);     
    return {"status": "success", "message": "登录成功","data":base_connect.id}

@router.post("/relogin")
def relogin_coros(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    重新登录并更新认证信息。
    成功后将更新 accessToken 到对应的连接表中。
    """
    if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}
    base_connect = base_connect_service.perform_relogin(connect_id,db, current_user);
    
    return {"status": "success", "message": "重新登录成功","data":base_connect.id}

@router.post("/pullFullActivities")
def pull_full_activities(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    全量获取运动记录并保存到本地数据库。
    采用分页拉取逻辑，通过 labelId 进行去重判断。
    """
    return base_activity_service.pull_full_activities(connect_id, incremental=False, db=db, current_user=current_user)

@router.post("/pullNewActivities")
def pull_new_activities(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    全量获取高驰运动记录并保存到本地数据库。
    采用分页拉取逻辑，通过 labelId 进行去重判断。
    """
    return base_activity_service.pull_full_activities(connect_id, incremental=True, db=db, current_user=current_user)

@router.get("/downloadActivity/{id}")
def download_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """    
    通过 activity_id 定位具体的运动记录，并返回文件内容。
    """
    return base_activity_service.download_activity(id,db, current_user)

@router.post("/uploadActivity2Target")
def upload_activity_to_target(
    id: int,
    target_platform: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从活动所属区域下载 FIT，上传到另一区域佳明账号（国际↔中国）。
    """
    return base_activity_service.upload_activity_to_target(id,target_platform,db, current_user)