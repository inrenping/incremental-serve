
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.models.garmin_connect import GarminConnect
from app.models.coros_connect import CorosConnect
from app.core.security import get_current_user


router = APIRouter()

def format_datetime(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")

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
    }
    if garmin_global:
        garmin_item.update({
            "email": garmin_global.garmin_account,
            "addedAt": format_datetime(garmin_global.created_at),
            "status": "验证通过" if garmin_global.is_active else "已失效",
            "region": "国际区",
            "lastUpdate": format_datetime(garmin_global.updated_at)
        })
    results.append(garmin_item)

    # --- Garmin Connect (CN) ---
    garmin_cn_item = {
        "id": "garmin_cn",
        "label": "Garmin Connect (CN)",
        "description": "连接您的 Garmin Connect (中国) 账号",
        "isConnected": garmin_cn.is_active if garmin_cn else False,
    }
    if garmin_cn:
        garmin_cn_item.update({
            "email": garmin_cn.garmin_account,
            "addedAt": format_datetime(garmin_cn.created_at),
            "status": "验证通过" if garmin_cn.is_active else "已失效",
            "region": "中国区",
            "lastUpdate": format_datetime(garmin_cn.updated_at)
        })
    results.append(garmin_cn_item)

    # --- Coros ---
    coros_item = {
        "id": "coros",
        "label": "Coros",
        "description": "连接您的 Coros 账号",
        "isConnected": coros_config.is_active if coros_config else False,
    }
    # Coros 详情后续可以按需在此补充
    results.append(coros_item)

    return results