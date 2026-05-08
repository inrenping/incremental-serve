from typing import Any, Optional, Tuple
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.core.security import get_current_user
from app.services import garmin_service

router = APIRouter()

# --- 定义前端请求的数据结构 ---

class OAuth1Data(BaseModel):
    """Garmin OAuth 1.0 凭证数据模型"""
    oauth_token: str
    oauth_token_secret: str

class OAuth2Data(BaseModel):
    """Garmin OAuth 2.0 令牌数据模型"""
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_token_expires_at: float

class TokenData(BaseModel):
    """完整的 Garmin Token 数据包，包含 OAuth1 和 OAuth2"""
    oauth1: OAuth1Data
    oauth2: OAuth2Data
    session: Optional[Any] = None

class GarminSaveRequest(BaseModel):
    """保存 Garmin 授权配置的请求体"""
    tokenData: TokenData
    username: Optional[str] = None # 对应 garmin_account
    password: Optional[str] = None # 对应 garmin_password

@router.get("/getConfig")
def get_garmin_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的 Garmin 授权配置列表。
    由于支持不同区域（CN/GLOBAL），一个用户可能拥有多个配置。
    """
    configs = garmin_service.get_garmin_configs(db, current_user.user_id)

    return {
        "status": "success",
        "data": [
            {
                "id": c.id,
                "region": c.region,
                "garmin_guid": c.garmin_guid,
                "garmin_display_name": c.garmin_display_name,
                "is_active": c.is_active,
                "last_synced_at": c.last_synced_at,
                "updated_at": c.updated_at
            }
            for c in configs
        ]
    }

@router.post("/saveConfig")
def save_garmin_config(
    payload: GarminSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从前端保存 Garmin 授权配置。
    解析 JWT 自动识别 region 和 garmin_guid，并绑定到当前用户。
    """
    data = garmin_service.save_garmin_auth_config(
        db=db,
        user_id=current_user.user_id,
        token_data=payload.tokenData,
        username=payload.username,
        password=payload.password
    )
    
    return {
        "status": "success",
        "data": data
    }

@router.post("/login")
def login_garmin(    
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    给前端返回用户名密码，让前端去刷新认证
    """
    configs = garmin_service.get_garmin_configs(db, current_user.user_id)
    if not configs:
        raise HTTPException(status_code=404, detail="No Garmin configuration found for the user.")
    return {
        "status": "success",
        "data": [
            {
                "username": config.garmin_account,
                "password": config.garmin_password,
                "platform": config.region
            }
            for config in configs
        ]
    }

@router.get("/pullFullActivities")
def pull_full_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    return garmin_service.pull_full_garmin_activities(db, current_user.user_id, region,True)

@router.get("/pullNewActivities")
def pull_new_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    return garmin_service.pull_full_garmin_activities(db, current_user.user_id, region,False)

@router.get("/saveNewActivities")
def save_new_activities(
    region: str = "CN",
    new_count:int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取最新的运动数据并保存。
    默认获取最近的 10 条记录，用于快速增量同步。
    """
    return garmin_service.sync_new_garmin_activities(
        db=db,
        user_id=current_user.user_id,
        region=region,
        limit=new_count
    )

@router.get("/downloadActivity/{id}")
def download_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    下载佳明运动记录的原文件 (FIT)。
    """
    file_response, filename = garmin_service.get_garmin_activity_download_info(db, current_user.user_id, id)
    return StreamingResponse(
        file_response.iter_content(chunk_size=8192),
        media_type=file_response.headers.get("Content-Type", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/uploadGarminActivity2Garmin/{id}")
def upload_garmin_activity_to_garmin(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从活动所属区域下载 FIT，上传到另一区域佳明账号（国际↔中国）。
    需在两个区域分别完成绑定并持有有效 token。
    """
    return garmin_service.sync_garmin_to_garmin(db, current_user.user_id, id)

@router.post("/uploadCorosActivity2Garmin/{id}")
def upload_coros_activity_to_garmin(
    id: int, 
    region: str = Query("CN", description="上传目标佳明账号区域: CN 或 GLOBAL"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    按 /coros/downloadActivity 同源流程从高驰获取 FIT，再上传到当前用户绑定的佳明账号。
    """
    return garmin_service.sync_coros_to_garmin(db, current_user.user_id, id, target_region=region)