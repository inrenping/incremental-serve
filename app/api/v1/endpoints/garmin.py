from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
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
    username: Optional[str] = None
    password: Optional[str] = None


class GarminLoginRequest(BaseModel):
    """高驰登录请求模型"""

    email: str
    password: str


@router.post("/login")
def login_garmin(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    通过用户名密码模拟登录
    """
    configs = garmin_service.get_garmin_configs(db, current_user.id)
    if not configs:
        raise HTTPException(
            status_code=404, detail="No Garmin configuration found for the user."
        )
    return {
        "status": "success",
        "data": [
            {
                "username": config.garmin_account,
                "password": config.garmin_password,
                "platform": config.region,
            }
            for config in configs
        ],
    }


@router.post("/getGarminSecretString")
def get_garmin_secret_string(
    connect_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    模拟 Garmin 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 garmin_connect 表。
    """
    try:
        updated_auth = garmin_service.refresh_garmin_secret_string(
            connect_id, db, current_user
        )
        return {
            "status": "success",
            "data": {
                "garmin_user_id": updated_auth.user_id,
                "region_id": updated_auth.region,
            },
        }
    except HTTPException as e:
        raise e


@router.post("/getGarminAccessTokenBySecertString")
def get_garmin_access_token_by_secret_string(
    connectId: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_connect = garmin_service.refresh_garmin_access_token(
        connectId, db, current_user
    )
    if not base_connect:
        return {"status": "error", "message": "未找到高驰授权配置，请先登录获取授权。"}
    return {"status": "success", "access_token": base_connect.access_token}


@router.post("/saveConfig")
def save_garmin_config(
    payload: GarminSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从前端保存 Garmin 授权配置。
    解析 JWT 自动识别 region 和 garmin_guid，并绑定到当前用户。
    """
    garmin_auth = garmin_service.save_garmin_connection(
        db=db,
        user_id=current_user.id,
        token_data=payload.tokenData,
        username=payload.username,
        password=payload.password,
    )

    return {
        "status": "success",
        "data": {"region": garmin_auth.region, "garmin_guid": garmin_auth.guid},
    }


@router.get("/refreshGarminActivityCount")
def refresh_garmin_activity_count(db: Session = Depends(get_db)):
    """刷新数字"""
    garmin_service.refresh_garmin_activity_count(db)
    return {"status": "success"}


@router.get("/saveNewActivities")
def save_new_activities(
    region: str = "CN",
    new_count: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从佳明接口获取最新的运动数据并保存。
    默认获取最近的 10 条记录，用于快速增量同步。
    """
    return garmin_service.sync_new_garmin_activities(
        db=db, user_id=current_user.id, region=region, limit=new_count
    )


@router.get("/downloadActivity/{id}")
def download_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    下载佳明运动记录的原文件，支持流式传输。
    """
    # 1. 获取 Response 对象（此时连接仍处于 open 状态）
    file_response, filename = garmin_service.get_garmin_activity_download_info(
        db, current_user, id
    )

    # 2. 定义生成器，确保在传输完成后关闭连接
    def stream_contents():
        try:
            # 这里的 .iter_content 是 requests 对象的方法
            for chunk in file_response.iter_content(chunk_size=8192):
                yield chunk
        finally:
            # 无论传输成功还是客户端断开，都关闭与佳明的连接
            file_response.close()

    return StreamingResponse(
        stream_contents(),
        media_type="application/zip",  # 佳明原始文件通常是压缩包
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    return garmin_service.sync_garmin_to_garmin(db, current_user, id)


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
    region = region.upper()
    return garmin_service.sync_coros_to_garmin(
        db, current_user, id, target_region=region
    )
