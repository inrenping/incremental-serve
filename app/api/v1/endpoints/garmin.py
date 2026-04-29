import json
import base64
import requests
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.models.garmin_activity import GarminActivity
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

@router.get("/refreshToken")
def refresh_token(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
  ):
    """
    调用 GarminConnect 模拟登录 获取相关数据存入数据库

    """
    return '';

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
    garmin_auth.garmin_account = payload.username
    # TODO：密码加密存储
    garmin_auth.garmin_password = payload.password 
    

    db.commit()

    return {
        "status": "success",
        "data": {
            "region": region,
            "garmin_guid": garmin_guid
        }
    }

@router.get("/fetchActivities")
def fetch_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    测试接口：根据数据库中的 Token 获取 Garmin 运动数据。
    """
    config = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.region == region
    ).first()

    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    # 根据区域决定 API 域名

    base_url = "connect.garmin.cn" if config.region == "CN" else "connect.garmin.com"
    api_url = f"https://{base_url}/activitylist-service/activities/search/activities"
    
    params = {
        "start": 0,
        "limit": 5,
        "activityType": "running", 
        "startDate": "2026-01-01"  
    }
    
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Accept": "application/json",
        "di-backend": base_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # print(f"请求 URL: {api_url}")
    # print(f"请求 Headers: {headers}")
    # print(f"请求 Params: {params}")

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"请求 Garmin 接口失败: {str(e)}")

    return {
        "status": "success",
        "data": {
            "activities": data
        }
    }



@router.get("/saveActivities")
def save_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取运动数据并保存到本地数据库。
    """
    config = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.region == region
    ).first()

    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    base_url = "connect.garmin.cn" if config.region == "CN" else "connect.garmin.com"
    api_url = f"https://{base_url}/activitylist-service/activities/search/activities"
    
    params = {
        "start": 0,
        "limit": 20,
    }
    
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Accept": "application/json",
        "di-backend": base_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        activities_data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步数据失败: {str(e)}")

    saved_count = 0
    for item in activities_data:
        activity_id = item.get("activityId")
        # 检查是否已存在（避免重复拉取）
        existing = db.query(GarminActivity).filter(GarminActivity.activity_id == activity_id).first()
        if existing:
            continue

        new_activity = GarminActivity(
            user_id=current_user.user_id,
            garmin_connect_id=config.id,
            activity_id=activity_id,
            activity_name=item.get("activityName"),
            activity_type_key=item.get("activityType", {}).get("typeKey"),
            start_time_local=item.get("startTimeLocal"),
            start_time_gmt=item.get("startTimeGMT"),
            distance_meters=item.get("distance"),
            duration_seconds=item.get("duration"),
            moving_duration_seconds=item.get("movingDuration"),
            calories=item.get("calories"),
            average_hr=item.get("averageHR"),
            max_hr=item.get("maxHR"),
            average_cadence=item.get("averageRunningCadenceInStepsPerMinute") or item.get("averageBikingCadenceInRevPerMinute"),
            max_cadence=item.get("maxRunningCadenceInStepsPerMinute") or item.get("maxBikingCadenceInRevPerMinute"),
            average_speed=item.get("averageSpeed"),
            max_speed=item.get("maxSpeed"),
            start_lat=item.get("startLatitude"),
            start_lon=item.get("startLongitude"),
            location_name=item.get("locationName"),
            device_id=str(item.get("deviceId")) if item.get("deviceId") else None,
        )
        db.add(new_activity)
        saved_count += 1

    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "success", "saved_count": saved_count}