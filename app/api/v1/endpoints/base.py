

from typing import Optional

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.user import User

from app.services import base_connect_service,base_activity_service
from pydantic import BaseModel

router = APIRouter()

class LoginRequest(BaseModel):
    """登录请求模型"""
    region: str
    email: str
    password: str


@router.get("/getConnectConfigs")
def get_connect_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的连接配置。
    """
    connect_configs = base_connect_service.get_connect_configs(db, current_user);
    
    return {"status": "success", "data": connect_configs}

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
    base_connect = base_connect_service.perform_login(login_request.email,login_request.password,login_request.region,db, current_user);     
    return {"status": "success", "message": "登录成功","data":base_connect.id}

@router.post("/reloginConnect")
def relogin_connect(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    重新登录指定的第三方平台连接并更新认证信息。
    成功后将更新 accessToken 或其他凭证到对应的 BaseConnect 记录中。
    Args:
        connect_id (int, optional): 要重新登录的连接ID。如果为空，则无法重新登录。
        current_user (User): 当前认证用户。
        db (Session): 数据库会话。
    Returns:
        dict: 包含状态、消息和更新后的连接ID。
    """
    if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}
    base_connect = base_connect_service.perform_relogin(connect_id,db, current_user);
    
    return {"status": "success", "message": "重新登录成功","data":base_connect.id}

@router.get("/getActivitiesByPage")
def get_activities_by_page(
    connect_id: int,
    pageSize: int = 10,
    pageCount: int = 1,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
  if not connect_id:
      return {"status": "error", "message": "缺少 connect_id 参数，无法获取活动列表。"}
  base_connect = db.query(BaseConnect).filter(BaseConnect.id == connect_id,BaseConnect.user_id == current_user.user_id).first()
  if not base_connect:
      return {"status": "success", "data": [], "total": 0}

  # Filter BaseActivity by user_id and source_provider from the BaseConnect object.
  # Note: The original code used BaseActivity.garmin_connect_id == base_connect.id,
  # which implies a direct link for Garmin. For a generic BaseActivity,
  # filtering by source_provider and user_id is more appropriate.
  # If BaseActivity has specific connect_id fields (e.g., garmin_connect_id, coros_connect_id),
  # the query needs to dynamically select the correct field based on base_connect.source_type.
  query = db.query(BaseActivity).filter(
      BaseActivity.user_id == current_user.user_id,
      BaseActivity.source_provider == base_connect.source_type)
  total = query.count()
  if startDate:
      query = query.filter(BaseActivity.start_time_local >= startDate)
  if endDate:
      query = query.filter(BaseActivity.start_time_local <= endDate)
  result = query.order_by(desc(BaseActivity.start_time_local)).limit(pageSize).offset((pageCount - 1) * pageSize).all()
  return {"status": "success", "data": result, "total": total}


@router.get("/getActivity")
def get_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
  activity = db.query(BaseActivity).filter(
            BaseActivity.user_id == current_user.user_id,
            BaseActivity.id == id,
        ).first()
  if not activity:
      raise HTTPException(status_code=404, detail="未找到对应的活动记录")
  return {"status": "success", "data": activity}

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