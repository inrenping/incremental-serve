import json
import base64
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple, Any, List
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.garmin_connect import GarminConnect
from app.models.garmin_activity import GarminActivity
from app.models.coros_connect import CorosConnect
from app.models.coros_activity import CorosActivity
from app.services import coros_service

GARMIN_UPLOAD_API_DOMAIN = {"CN": "connectapi.garmin.cn", "GLOBAL": "connectapi.garmin.com"}

def get_garmin_configs(db: Session, user_id: int) -> List[GarminConnect]:
    """获取指定用户的所有佳明授权配置。"""
    return db.query(GarminConnect).filter(GarminConnect.user_id == user_id).all()


def update_garmin_count(db: Session, garmin_connect_id: int, total_count: int) -> bool:
    """更新 GarminConnect 中对应的 total_count 的值。"""
    garmin_auth = db.query(GarminConnect).filter(GarminConnect.id == garmin_connect_id).first()
    if garmin_auth:
        garmin_auth.total_count = total_count
        db.commit()
        return True
    return False

def save_garmin_auth_config(
    db: Session, 
    user_id: int, 
    token_data: Any, 
    username: Optional[str] = None, 
    password: Optional[str] = None
) -> dict:
    """解析并保存 Garmin 授权配置。"""
    oauth2 = token_data.oauth2
    oauth1 = token_data.oauth1

    try:
        _, payload_b64, _ = oauth2.access_token.split('.')
        missing_padding = len(payload_b64) % 4
        if missing_padding:
            payload_b64 += '=' * (4 - missing_padding)
        
        decoded_payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        garmin_guid = decoded_payload.get("garmin_guid")
        iss = decoded_payload.get("iss", "")

        region = "GLOBAL" if "garmin.com" in iss else "CN"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析 Garmin Token 失败: {str(e)}")

    garmin_auth = db.query(GarminConnect).filter(
        GarminConnect.user_id == user_id,
        GarminConnect.region == region
    ).first()

    if not garmin_auth:
        garmin_auth = GarminConnect(user_id=user_id, region=region)
        db.add(garmin_auth)

    garmin_auth.garmin_guid = garmin_guid
    garmin_auth.oauth_token = oauth1.oauth_token
    garmin_auth.oauth_token_secret = oauth1.oauth_token_secret
    garmin_auth.access_token = oauth2.access_token
    garmin_auth.refresh_token = oauth2.refresh_token
    garmin_auth.access_token_expires_at = datetime.fromtimestamp(oauth2.expires_at)
    garmin_auth.refresh_token_expires_at = datetime.fromtimestamp(oauth2.refresh_token_expires_at)
    garmin_auth.is_active = True
    garmin_auth.garmin_account = username
    garmin_auth.garmin_password = password     
    garmin_auth.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"region": region, "garmin_guid": garmin_guid}

def _sync_garmin_activities_internal(
    db: Session,
    config: GarminConnect,
    user_id: int,
    start: int = 0,
    limit: int = 100,
    incremental: bool = True
) -> Tuple[int, int]:
    """辅助方法：抓取并保存活动。"""
    base_url = "connect.garmin.cn" if config.region == "CN" else "connect.garmin.com"
    api_url = f"https://{base_url}/activitylist-service/activities/search/activities"
    
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "di-backend": base_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(api_url, params={"start": start, "limit": limit}, headers=headers, timeout=10)
        response.raise_for_status()
        activities_data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步佳明数据失败: {str(e)}")

    if not activities_data or not isinstance(activities_data, list):
        return 0, 0

    activity_ids = [item.get("activityId") for item in activities_data if item.get("activityId")]
    existing_ids = {aid for (aid,) in db.query(GarminActivity.activity_id).filter(GarminActivity.activity_id.in_(activity_ids)).all()} if activity_ids else set()

    saved_count = 0
    for item in activities_data:
        activity_id = item.get("activityId")
        if activity_id in existing_ids:
            if incremental:
                # 增量同步模式下，遇到已存在记录即停止本批次后续处理
                break
            continue

        new_activity = GarminActivity(
            user_id=user_id, garmin_connect_id=config.id, activity_id=activity_id,
            activity_name=item.get("activityName"), activity_type_key=item.get("activityType", {}).get("typeKey"),
            start_time_local=item.get("startTimeLocal"), start_time_gmt=item.get("startTimeGMT"),
            distance_meters=item.get("distance"), duration_seconds=item.get("duration"),
            moving_duration_seconds=item.get("movingDuration"), calories=item.get("calories"),
            average_hr=item.get("averageHR"), max_hr=item.get("maxHR"),
            average_cadence=item.get("averageRunningCadenceInStepsPerMinute") or item.get("averageBikingCadenceInRevPerMinute"),
            max_cadence=item.get("maxRunningCadenceInStepsPerMinute") or item.get("maxBikingCadenceInRevPerMinute"),
            average_speed=item.get("averageSpeed"), max_speed=item.get("maxSpeed"),
            start_lat=item.get("startLatitude"), start_lon=item.get("startLongitude"),
            location_name=item.get("locationName"), device_id=str(item.get("deviceId")) if item.get("deviceId") else None,
            elevation_gain=item.get("elevationGain"), elevation_loss=item.get("elevationLoss")
        )
        db.add(new_activity)
        saved_count += 1

    return len(activities_data), saved_count

def pull_full_garmin_activities(db: Session, user_id: int, region: str,incremental : bool =True) -> dict:
    """全量或者增量同步佳明活动。"""
    config = db.query(GarminConnect).filter(GarminConnect.user_id == user_id, GarminConnect.region == region).first()
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    start, limit, total_saved, total_fetched = 0, 100, 0, 0
    while True:
        fetched, saved = _sync_garmin_activities_internal(db, config, user_id, start, limit,incremental)
        total_fetched += fetched
        total_saved += saved
        if fetched < limit: break
        
        # 如果是增量同步且本页保存数量小于获取数量，说明遇到了重复数据，停止分页获取
        if incremental and saved < fetched:
            break
        start += limit

    if total_fetched: update_garmin_count(db, config.id, total_fetched)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "fetched_count": total_fetched, "saved_count": total_saved}

def sync_new_garmin_activities(db: Session, user_id: int, region: str, limit: int = 10) -> dict:
    """增量同步最新佳明活动。"""
    config = db.query(GarminConnect).filter(GarminConnect.user_id == user_id, GarminConnect.region == region).first()
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    fetched, saved = _sync_garmin_activities_internal(db, config, user_id, 0, limit)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "fetched_count": fetched, "saved_count": saved}

def get_garmin_activity_download_info(db: Session, user_id: int, activity_id: int) -> Tuple[requests.Response, str]:
    """获取佳明 FIT 文件下载。"""
    ga = db.query(GarminActivity).filter(GarminActivity.user_id == user_id, GarminActivity.id == activity_id).first()
    if not ga:
        raise HTTPException(status_code=404, detail="未找到同步记录，请刷新后重试")

    garmin_auth = ga.garmin_connect
    if not garmin_auth or not garmin_auth.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的佳明授权配置，请检查账号绑定状态")

    base = "connect.garmin.cn" if garmin_auth.region == "CN" else "connect.garmin.com"
    url = f"https://{base}/download-service/files/activity/{ga.activity_id}"
    
    headers = {
        "di-backend": base,
        "Authorization": f"Bearer {garmin_auth.access_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        print(f"正在下载 Garmin 活动 {ga.activity_id}，URL: {url}")
        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        print(f"下载佳明活动 {ga.activity_id}，HTTP 状态码: {resp.status_code}")
        # if len(resp.content) < 10000:  
        #        print(f"下载到的 Garmin 文件可能不完整，大小: {len(resp.content)} 字节")        
        if resp.status_code != 200:
            # print(f"下载 Garmin 活动 {ga.activity_id} 失败，HTTP 状态码: {resp.status_code}，响应内容: {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail="文件下载失败，服务器返回错误")
            
        return resp, f"activity_{ga.activity_id}.zip"
    except HTTPException:
        raise
    except requests.exceptions.ConnectionError:
        # 处理网络连接错误（如 DNS 污染或被墙）
        raise HTTPException(status_code=502, detail="网络连接错误，请检查网络环境或尝试使用代理")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"佳明文件下载失败: {str(e)}")

def parse_garmin_upload_response(response: requests.Response) -> Tuple[str, Optional[dict]]:
    """解析佳明上传接口响应。"""
    try:
        result = response.json()
    except:
        result = None

    if response.status_code == 202 and result:
        if (result.get("detailedImportResult") or {}).get("uploadId"):
            return "SUCCESS", result
        return "UPLOAD_REJECTED", result

    if response.status_code == 409 and result:
        try:
            msg = result.get("detailedImportResult", {}).get("failures", [{}])[0].get("messages", [{}])[0].get("content")
            if msg == "Duplicate Activity.": return "DUPLICATE_ACTIVITY", result
        except: pass
        return "UPLOAD_CONFLICT", result

    return "UPLOAD_FAILED" if result else "UPLOAD_EXCEPTION", result

def sync_garmin_to_garmin(db: Session, user_id: int, activity_id: int) -> dict:
    """佳明跨区同步逻辑。"""
    ga = db.query(GarminActivity).filter(GarminActivity.user_id == user_id, GarminActivity.id == activity_id).first()
    if not ga: raise HTTPException(status_code=404, detail="记录不存在")

    source_region = ga.garmin_connect.region or "CN"
    target_region = "GLOBAL" if source_region == "CN" else "CN"

    target_config = db.query(GarminConnect).filter(GarminConnect.user_id == user_id, GarminConnect.region == target_region).first()
    if not target_config: raise HTTPException(status_code=404, detail="目标区域未授权")

    file_resp, filename = get_garmin_activity_download_info(db, user_id, activity_id)
    file_data = file_resp.content

    api_domain = GARMIN_UPLOAD_API_DOMAIN.get(target_region, "connectapi.garmin.com")
    headers = {"Authorization": f"Bearer {target_config.access_token}", "di-backend": "connect.garmin.cn" if target_region == "CN" else "connect.garmin.com"}
    url = f"https://{api_domain}/upload-service/upload"
    resp = requests.post(url, headers=headers, files={"file": (filename, file_data, "application/octet-stream")}, timeout=60)
    status, json_res = parse_garmin_upload_response(resp)
    # print(f"佳明上传活动 {url} | {ga.activity_id} 到 {target_region}，HTTP 状态码: {resp.status_code}，解析结果: {json.dumps(json_res)}")
    return {"status": "success", "upload_status": status, "target_region": target_region, "http_status": resp.status_code, "garmin_response": json_res}

def sync_coros_to_garmin(db: Session, user_id: int, coros_activity_id: int, target_region: str) -> dict:
    """高驰同步到佳明逻辑。"""
    target_config = db.query(GarminConnect).filter(GarminConnect.user_id == user_id, GarminConnect.region == target_region).first()
    if not target_config: raise HTTPException(status_code=404, detail="目标佳明区域未授权")

    file_resp, filename = coros_service.get_coros_activity_download_info(db, user_id, coros_activity_id)
    file_data = file_resp.content

    api_domain = GARMIN_UPLOAD_API_DOMAIN.get(target_region, "connectapi.garmin.com")
    headers = {"Authorization": f"Bearer {target_config.access_token}", "di-backend": "connect.garmin.cn" if target_region == "CN" else "connect.garmin.com"}
    url = f"https://{api_domain}/upload-service/upload"
    resp = requests.post(url, headers=headers, files={"file": (filename, file_data, "application/octet-stream")}, timeout=60)
    status, json_res = parse_garmin_upload_response(resp)
    # print(f"佳明上传活动{url} | {coros_activity_id} 到 {target_region}，HTTP 状态码: {resp.status_code}，解析结果: {json.dumps(json_res)}")
    return {"status": "success", "upload_status": status, "target_region": target_region, "http_status": resp.status_code, "garmin_response": json_res}


def refresh_garmin_activity_count(db: Session) -> dict:
    """刷新所有用户的佳明活动总数统计。"""     
    users = db.query(GarminConnect.user_id).distinct().all()
    for (user_id,) in users:
      garmin_auths = db.query(GarminConnect).filter(GarminConnect.user_id == user_id)
      for garmin_auth in garmin_auths:
        activity_count = db.query(GarminActivity).filter(GarminActivity.user_id == user_id, GarminActivity.garmin_connect_id == garmin_auth.id).count()
        update_garmin_count(db, garmin_auth.id, activity_count)
     