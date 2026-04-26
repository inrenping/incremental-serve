import json
import base64
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.core.security import get_current_user

router = APIRouter()

# --- 定义前端请求的数据结构 ---

class OAuth1Data(BaseModel):
    oauth_token: str
    oauth_token_secret: str

class OAuth2Data(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_token_expires_at: float

class TokenData(BaseModel):
    oauth1: OAuth1Data
    oauth2: OAuth2Data
    session: Optional[Any] = None

class GarminSaveRequest(BaseModel):
    tokenData: TokenData

@router.get("/getConfig")
def get_garmin_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的 Garmin 授权配置列表。
    由于支持不同区域（CN/GLOBAL），一个用户可能拥有多个配置。
    """
    configs = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id
    ).all()

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

@router.post("/save")
def save_garmin_config(
    payload: GarminSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从前端保存 Garmin 授权配置。
    解析 JWT 自动识别 region 和 garmin_guid，并绑定到当前用户。
    """
    token_info = payload.tokenData
    oauth2 = token_info.oauth2
    oauth1 = token_info.oauth1

    try:
        # JWT 结构：header.payload.signature
        _, payload_b64, _ = oauth2.access_token.split('.')
        # 补全 Base64 填充
        missing_padding = len(payload_b64) % 4
        if missing_padding:
            payload_b64 += '=' * (4 - missing_padding)
        
        decoded_payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        garmin_guid = decoded_payload.get("garmin_guid")
        iss = decoded_payload.get("iss", "")

        # 根据发行者识别区域
        region = "CN"
        if "garmin.com" in iss:
            region = "GLOBAL"
        elif "garmin.cn" in iss:
            region = "CN"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析 Garmin Token 失败: {str(e)}")

    garmin_auth = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.region == region
    ).first()

    if not garmin_auth:
        garmin_auth = GarminConnect(user_id=current_user.user_id, region=region)
        db.add(garmin_auth)

    garmin_auth.region = region
    garmin_auth.garmin_guid = garmin_guid
    garmin_auth.oauth_token = oauth1.oauth_token
    garmin_auth.oauth_token_secret = oauth1.oauth_token_secret
    garmin_auth.access_token = oauth2.access_token
    garmin_auth.refresh_token = oauth2.refresh_token
    # 转换时间戳为 datetime (带时区)
    garmin_auth.access_token_expires_at = datetime.fromtimestamp(oauth2.expires_at, tz=timezone.utc)
    garmin_auth.refresh_token_expires_at = datetime.fromtimestamp(oauth2.refresh_token_expires_at, tz=timezone.utc)
    garmin_auth.is_active = True

    db.commit()

    return {
        "status": "success",
        "data": {
            "region": region,
            "garmin_guid": garmin_guid
        }
    }