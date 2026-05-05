from datetime import datetime
from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.coros_activity import CorosActivity
from app.models.garmin_activity import GarminActivity
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.models.coros_connect import CorosConnect
from app.core.security import get_current_user
from app.api.v1.endpoints.garmin import (
    save_all_activities as sync_garmin,
    save_new_activities as sync_new_garmin,
    download_garmin_activity as download_garmin
)
from app.api.v1.endpoints.coros import (
    save_all_activities as sync_coros,
    save_new_coros_activities as sync_new_coros,
    download_coros_activity as download_coros
)

router = APIRouter()

def format_datetime(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.isoformat()

def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return None
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
        "total_count": garmin_cn.total_count if garmin_cn else 0,
    }
    if garmin_cn:
        garmin_cn_item.update({
            "email": garmin_cn.garmin_account,
            "addedAt": format_datetime(garmin_cn.created_at),
            "status": "验证通过" if garmin_cn.is_active else "已失效",
            "region": "中国区",
            "lastUpdate": format_datetime(garmin_cn.updated_at),
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
            "total_count": coros_config.total_count,
        })
    results.append(coros_item)

    return results

@router.get("/getActivitiesWithPlatformByPage")
def get_activities_with_platform_by_page(
    platform: str,
    pageSize: int = 10,
    pageCount: int = 1,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """分页获取运动记录，支持不同平台查询。"""

    if platform in ["garmin", "garmin_cn"]:
        region_filter = "GLOBAL" if platform == "garmin" else "CN"

        # 获取该用户的 Garmin 授权配置，避免联合查询
        config = db.query(GarminConnect).filter(
            GarminConnect.user_id == current_user.user_id,
            GarminConnect.region == region_filter
        ).first()

        if not config:
            return {"status": "success", "data": [], "total": 0}

        query = (
            db.query(GarminActivity)
            .filter(GarminActivity.garmin_connect_id == config.id)
        )

        if startDate:
            query = query.filter(GarminActivity.start_time_local >= startDate)
        if endDate:
            query = query.filter(GarminActivity.start_time_local <= endDate)

        total = query.count()

        garminActivities = (
            query.order_by(desc(GarminActivity.start_time_local))
            .limit(pageSize)
            .offset((pageCount - 1) * pageSize)
            .all()
        )
        
        data = []
        for activity in garminActivities:
            data.append({
                "id": activity.id,
                "title": activity.activity_name,
                "startTime": activity.start_time_local,
                "type": activity.activity_type_key,
                "workoutTime": format_duration(activity.moving_duration_seconds),
                "totalTime": format_duration(activity.duration_seconds),
                "distance": f"{float(activity.distance_meters or 0) / 1000:.2f} km",
                "elevation": f"{int(activity.elevation_gain or 0)} m",
                "platform": region_filter,
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

        if startDate:
            query = query.filter(CorosActivity.start_time >= startDate)
        if endDate:
            query = query.filter(CorosActivity.start_time <= endDate)

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
                "id":activity.id,
                "title": activity.name,
                "startTime": activity.start_time,
                "type": str(activity.sport_type), 
                "workoutTime": format_duration(activity.workout_time),
                "totalTime": format_duration(activity.total_time),
                "distance": f"{float(activity.distance or 0) / 1000:.2f} km",
                "elevation": f"{(activity.ascent or 0)} m",
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
    

@router.post("/syncAllActivities")
def sync_all_activities(
    platform: str = "garmin",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    同步下载指定平台的运动记录。
    根据 platform 参数分别调用 Garmin (Global/CN) 或 Coros 的同步逻辑。
    """
    if platform == "garmin":
        # 调用 Garmin 国际区同步接口
        return sync_garmin(region="GLOBAL", current_user=current_user, db=db)
    elif platform == "garmin_cn":
        # 调用 Garmin 中国区同步接口
        return sync_garmin(region="CN", current_user=current_user, db=db)
    elif platform == "coros":
        # 调用 Coros 同步接口
        return sync_coros(current_user=current_user, db=db)
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")
    
@router.post("/syncNewActivities")
def sync_new_activities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    同步所有已连接平台的最新运动记录。
    此接口会尝试为用户绑定的所有活跃平台（Garmin Global, Garmin CN, Coros）触发增量同步。
    """
    results = {}

    # 1. 获取并处理 Garmin 配置 (可能包含多个区域)
    garmin_configs = db.query(GarminConnect).filter(
        GarminConnect.user_id == current_user.user_id,
        GarminConnect.is_active == True
    ).all()

    # 2. 获取并处理 Coros 配置
    coros_auth = db.query(CorosConnect).filter(
        CorosConnect.user_id == current_user.user_id,
        CorosConnect.is_active == True
    ).first()

    # 校验：检查用户是否绑定了运动平台
    # 这里判断逻辑为：如果当前没有任何活跃的平台连接，则提示用户先绑定
    active_platforms_count = len(garmin_configs) + (1 if coros_auth else 0)
    if active_platforms_count < 2:
        raise HTTPException(
            status_code=400, 
            detail="请先前往设置页面绑定 Garmin 或 Coros 账号，再进行同步。"
        )

    for config in garmin_configs:
        platform_key = f"garmin_{config.region.lower()}"
        try:
            # 调用 Garmin 增量同步
            res = sync_new_garmin(region=config.region, current_user=current_user, db=db)
            results[platform_key] = res
        except Exception as e:
            results[platform_key] = {"status": "error", "detail": str(e)}

    if coros_auth:
        try:
            # 调用 Coros 增量同步
            res = sync_new_coros(current_user=current_user, db=db)
            results["coros"] = res
        except Exception as e:
            results["coros"] = {"status": "error", "detail": str(e)}

    return {
        "status": "success",
        "results": results
    }

    
@router.get("/downloadActivity")
def download_activity(
    id: int,
    platform: str = "coros",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    通用运动记录下载接口。
    根据 platform 参数分发到对应的平台下载逻辑。
    """
    if platform.lower() == "coros":
        return download_coros(id=id, current_user=current_user, db=db)
    return download_garmin(id=id, current_user=current_user, db=db)