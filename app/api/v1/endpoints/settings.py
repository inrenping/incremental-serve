
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.coros_activity import CorosActivity
from app.models.garmin_activity import GarminActivity
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.models.coros_connect import CorosConnect
from app.core.security import get_current_user


router = APIRouter()

def format_datetime(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.isoformat()

def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "00:00"
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

@router.get("/getAppsConfigs")
def get_apps_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的第三方 App 授权状态（Garmin, Garmin CN, Coros）。
    """
    # 1. 获取 Garmin 配置
    garmin_configs = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id
    ).all()
    
    garmin_global = next((c for c in garmin_configs if c.region == "GLOBAL"), None)
    garmin_cn = next((c for c in garmin_configs if c.region == "CN"), None)

    # 2. 获取 Coros 配置
    coros_config = db.query(CorosConnect).filter(
        CorosConnect.user_id == current_user.user_id
    ).first()

    results = []

    # --- Garmin Connect (Global) ---
    garmin_item = {
        "id": "garmin",
        "label": "Garmin Connect",
        "description": "连接您的 Garmin Connect 账号",
        "isConnected": garmin_global.is_active if garmin_global else False,
        "total_count": garmin_global.total_count if garmin_global else 0,
    }
    if garmin_global:
        garmin_item.update({
            "email": garmin_global.garmin_account,
            "addedAt": format_datetime(garmin_global.created_at),
            "status": "验证通过" if garmin_global.is_active else "已失效",
            "region": "国际区",
            "lastUpdate": format_datetime(garmin_global.updated_at),
        })
    results.append(garmin_item)

    # --- Garmin Connect (CN) ---
    garmin_cn_item = {
        "id": "garmin_cn",
        "label": "Garmin Connect (CN)",
        "description": "连接您的 Garmin Connect (中国) 账号",
        "isConnected": garmin_cn.is_active if garmin_cn else False,
        "total_count": garmin_global.total_count if garmin_global else 0,
    }
    if garmin_cn:
        garmin_cn_item.update({
            "email": garmin_cn.garmin_account,
            "addedAt": format_datetime(garmin_cn.created_at),
            "status": "验证通过" if garmin_cn.is_active else "已失效",
            "region": "中国区",
            "lastUpdate": format_datetime(garmin_cn.updated_at),
            "total_count": garmin_cn.total_count if garmin_cn else 0,
        })
    results.append(garmin_cn_item)

    # --- Coros ---
    coros_item = {
        "id": "coros",
        "label": "Coros",
        "description": "连接您的 Coros 账号",
        "isConnected": coros_config.is_active if coros_config else False,
        "total_count": coros_config.total_count if coros_config else 0,
    }
    if coros_config:
        coros_item.update({
            "email": coros_config.coros_account,
            "addedAt": format_datetime(coros_config.created_at),
            "status": "验证通过" if coros_config.is_active else "已失效",
            "region":coros_config.region,
            "lastUpdate": format_datetime(coros_config.updated_at),
            "total_count": coros_config.total_count if coros_config else 0,
        })
    results.append(coros_item)

    return results

@router.get("/getActivitiesByPage")
def get_activities_by_page(
    platform: str,
    pageSize: int = 10,
    pageCount: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """分页获取运动记录，支持不同平台查询。"""

    if platform in ["garmin", "garmin_cn"]:
        region_filter = "GLOBAL" if platform == "garmin" else "CN"
        # 联合查询以获取账号所属区域，区分国际版和中国版
        query = (
            db.query(GarminActivity, GarminConnect.region)
            .join(GarminConnect, GarminActivity.garmin_connect_id == GarminConnect.id)
            .filter(GarminActivity.user_id == current_user.user_id)
            .filter(GarminConnect.region == region_filter)
        )

        total = query.count()

        results = (
            query.order_by(desc(GarminActivity.start_time_gmt))
            .limit(pageSize)
            .offset((pageCount - 1) * pageSize)
            .all()
        )
        
        data = []
        for activity, region in results:
            data.append({
                "title": activity.activity_name ,
                "date": format_datetime(activity.start_time_local),
                "time": activity.start_time_local.strftime("%H:%M") if activity.start_time_local else "",
                "type": activity.activity_type_key,
                "workoutTime": format_duration(activity.moving_duration_seconds),
                "totalTime": format_duration(activity.duration_seconds),
                "distance": f"{float(activity.distance_meters or 0) / 1000:.2f} km",
                "elevation": "--",
                "platform":  region ,
                "platformId": str(activity.activity_id),
                "syncTime": format_datetime(activity.updated_at)
            })
            
        return {
            "status": "success",
            "data": data,
            "total": total
        }
    elif platform == "coros":
        query = (
            db.query(CorosActivity)
            .filter(CorosActivity.user_id == current_user.user_id)
        )

        total = query.count()

        corosActivities = (
            query.order_by(desc(CorosActivity.start_time))
            .limit(pageSize)
            .offset((pageCount - 1) * pageSize)
            .all()
        )
        
        data = []
        for activity in corosActivities:
            data.append({
                "title": activity.name,
                "date": activity.start_time,
                "time": activity.start_time.strftime("%H:%M") if activity.start_time else "",
                "type": str(activity.sport_type), 
                "workoutTime": format_duration(activity.duration),
                "totalTime": format_duration(activity.duration),
                "distance": f"{float(activity.distance or 0) / 1000:.2f} km",
                "elevation": "0 m",
                "platform": "Coros",
                "platformId": activity.label_id,
                "syncTime": activity.updated_at
            })
            
        return {
            "status": "success",
            "data": data,
            "total": total
        }
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")
    

@router.get("/syncActivities")
def sync_activities(
    count: int = 10,
    platform: str = "garmin",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return "";