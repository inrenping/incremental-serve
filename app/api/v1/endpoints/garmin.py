import json
import base64
import requests
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
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
    # TODO
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
    garmin_auth.access_token_expires_at = datetime.fromtimestamp(oauth2.expires_at)
    garmin_auth.refresh_token_expires_at = datetime.fromtimestamp(oauth2.refresh_token_expires_at)
    garmin_auth.is_active = True
    garmin_auth.garmin_account = payload.username

    # TODO：密码加密存储(前端就要加密，后端使用的时候单独解密)
    garmin_auth.garmin_password = payload.password     

    db.commit()

    return {
        "status": "success",
        "data": {
            "region": region,
            "garmin_guid": garmin_guid
        }
    }

def _sync_garmin_activities(
    db: Session,
    config: GarminConnect,
    current_user: User,
    start: int = 0,
    limit: int = 100
):
    """辅助方法：抓取并保存佳明活动。返回 (获取到的记录数, 新保存的记录数)"""
    base_url = "connect.garmin.cn" if config.region == "CN" else "connect.garmin.com"
    api_url = f"https://{base_url}/activitylist-service/activities/search/activities"
    
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Accept": "application/json",
        "di-backend": base_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    params = {"start": start, "limit": limit}
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        activities_data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步佳明数据失败: {str(e)}")

    if not activities_data or not isinstance(activities_data, list):
        return 0, 0

    # 查重逻辑：批量查询数据库中已存在的 ID
    activity_ids = [item.get("activityId") for item in activities_data if item.get("activityId")]
    existing_ids = set()
    if activity_ids:
        existing_ids = {
            act_id for (act_id,) in db.query(GarminActivity.activity_id)
            .filter(GarminActivity.activity_id.in_(activity_ids))
            .all()
        }

    saved_count = 0
    for item in activities_data:
        activity_id = item.get("activityId")
        if activity_id in existing_ids:
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
            elevation_gain=item.get("elevationGain"),
            elevation_loss=item.get("elevationLoss")
        )
        db.add(new_activity)
        saved_count += 1

    return len(activities_data), saved_count

@router.get("/saveAllActivities")
def save_all_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    config = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.region == region
    ).first()

    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    start = 0
    limit = 100
    total_saved_count = 0
    total_fetched_count = 0

    while True:
        fetched, saved = _sync_garmin_activities(db, config, current_user, start, limit)
        total_fetched_count += fetched
        total_saved_count += saved

        if fetched < limit:
            break
        start += limit

    if total_fetched_count:
        update_garmin_count(db, config.id, total_fetched_count)

    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success", 
        "fetched_count": total_fetched_count, 
        "saved_count": total_saved_count
    }

def update_garmin_count(db: Session, garmin_connect_id: int, total_count: int):
    """
    更新 GarminConnect 中对应的 total_count 的值。
    """
    garmin_auth = db.query(GarminConnect).filter(GarminConnect.id == garmin_connect_id).first()
    if garmin_auth:
        garmin_auth.total_count = total_count
        db.commit()
        return True
    return False


@router.get("/saveNewActivities")
def save_new_activities(
    region: str = "CN",
    new_count:int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    config = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.region == region
    ).first()

    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    fetched, saved = _sync_garmin_activities(db, config, current_user, 0, new_count)

    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success", 
        "fetched_count": fetched, 
        "saved_count": saved
    }

@router.get("/downloadActivity/{id}")
def download_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    下载佳明运动记录的原文件 (FIT)。
    """
    # 1. 查找运动记录
    garmin_activity = db.query(GarminActivity).filter(
        GarminActivity.user_id == current_user.user_id,
        GarminActivity.id == id
    ).first()

    if not garmin_activity:
        raise HTTPException(status_code=404, detail="未找到同步记录，请刷新后重试")

    # 2. 获取关联的佳明授权配置
    garmin_auth = garmin_activity.garmin_connect

    if not garmin_auth or not garmin_auth.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的佳明授权配置，请检查账号绑定状态")
    
    # 3. 构造佳明原始文件 (FIT) 下载地址
    base_domain = "connect.garmin.cn" if garmin_auth.region == "CN" else "connect.garmin.com"
    download_url = f"https://{base_domain}/download-service/files/activity/{garmin_activity.activity_id}"

    print(f"[用户: {current_user.user_id}] 尝试下载活动: {id}, 构造URL: {download_url}")
    
    headers = {
        "di-backend": base_domain,
        "Authorization": f"Bearer {garmin_auth.access_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # 4. 执行文件下载流
        file_response = requests.get(download_url, headers=headers, stream=True, timeout=30)      

        # 检查状态码
        if file_response.status_code != 200:
            print(f"下载失败，HTTP状态码: {file_response.status_code}, 响应URL: {file_response.url}")
            raise HTTPException(status_code=file_response.status_code, detail="文件下载失败，服务器返回错误")

        filename = f"activity_{garmin_activity.activity_id}.fit"
        response_headers = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        upstream_content_type = file_response.headers.get("Content-Type", "application/octet-stream")

        return StreamingResponse(
            file_response.iter_content(chunk_size=8192),
            media_type=upstream_content_type,
            headers=response_headers,
        )
    except HTTPException:
        raise
    except requests.exceptions.ConnectionError as conn_err:
        # 这通常是网络层面的问题，比如 SSL 握手失败、连接被重置（常见于被墙的连接）
        print(f"网络连接错误 (可能是被墙或DNS污染): {str(conn_err)}")
        raise HTTPException(status_code=502, detail="网络连接错误，请检查网络环境或尝试使用代理")
    except Exception as e:
        print(f"未知错误: {str(e)}")
        raise HTTPException(status_code=400, detail=f"佳明文件下载失败: {str(e)}")

@router.get("/uploadActivity")
def upload_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return None;