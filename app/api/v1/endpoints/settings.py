from datetime import datetime, timezone, timedelta
from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.base_activity import BaseActivity
from app.models.user import User

router = APIRouter()


class OneClickSyncRequest(BaseModel):
    """一键同步请求模型"""

    source: str
    target: str


def download_garmin(id: int, current_user: User, db: Session):
    """
    Placeholder for downloading Garmin activity.
    The actual implementation is in app.api.v1.endpoints.garmin.download_garmin_activity.
    """
    from app.api.v1.endpoints.garmin import download_garmin_activity

    return download_garmin_activity(id=id, current_user=current_user, db=db)


def download_coros(id: int, current_user: User, db: Session):
    """
    Placeholder for downloading Coros activity.
    The actual implementation is in app.api.v1.endpoints.coros.download_coros_activity.
    """
    from app.api.v1.endpoints.coros import download_coros_activity

    return download_coros_activity(id=id, current_user=current_user, db=db)


def format_datetime(dt: Optional[datetime]) -> str:
    """将 datetime 对象转换为 ISO 格式字符串。如果输入为空，则返回空字符串。"""
    if not dt:
        return ""
    return dt.isoformat()


def format_duration(seconds: Optional[float]) -> str:
    """将秒数格式化为友好的时长字符串（H:MM:SS 或 MM:SS）。"""
    if seconds is None:
        return None
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_garmin_activity_dict(activity: BaseActivity, region: str) -> dict:
    """将 Garmin 活动模型转换为前端使用的统一字典格式。"""
    return {
        "id": activity.id,
        "title": activity.activity_name,
        "startTime": activity.start_time_local,  # Assuming GarminActivity has start_time_local
        "type": activity.activity_type_key,
        "workoutTime": format_duration(activity.moving_duration_seconds),
        "totalTime": format_duration(activity.duration_seconds),
        "distance": f"{float(activity.distance_meters or 0) / 1000:.2f} km",
        "elevation": f"{int(activity.elevation_gain or 0)} m",
        "platform": region,
        "platformId": str(activity.activity_id),
        "syncTime": format_datetime(activity.updated_at),
    }


def _format_coros_activity_dict(activity: BaseActivity) -> dict:
    """将 Coros 活动模型转换为前端使用的统一字典格式。"""
    return {
        "id": activity.id,
        "title": activity.name,
        "startTime": activity.start_time,  # Assuming CorosActivity has start_time
        "type": str(activity.sport_type),
        "workoutTime": format_duration(activity.workout_time),
        "totalTime": format_duration(activity.total_time),
        "distance": f"{float(activity.distance or 0) / 1000:.2f} km",
        "elevation": f"{(activity.ascent or 0)} m",
        "platform": "Coros",
        "platformId": activity.label_id,
        "syncTime": format_datetime(activity.updated_at),
    }


def is_same_activity(
    source_start_time: Optional[datetime],
    target_start_time: Optional[datetime],
) -> bool:
    """
    判断两条活动记录是否可视为同一条活动。

    判定规则：
    1. 起始时间任一为空时，直接返回 False。
    2. 起始时间差超过 5 分钟，返回 False。

    Args:
        source_start_time: 源平台活动开始时间。
        target_start_time: 目标平台活动开始时间。

    Returns:
        bool: True 表示两条记录可判定为同一活动，False 表示不是。
    """
    if source_start_time is None or target_start_time is None:
        return False

    time_diff_seconds = abs((source_start_time - target_start_time).total_seconds())
    if time_diff_seconds > 5 * 60:
        return False
    return True


def to_aware_utc(dt):
    """把 datetime 转成 UTC aware，如果是 naive 则假设是 UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
