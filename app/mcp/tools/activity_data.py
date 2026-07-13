"""
MCP Tool: get_activities

Return detailed activity records for a given time period,
with optional sport type filtering.
"""

from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.utils.activity_type_config import ACTIVITY_CONFIG


def _activity_to_dict(act: BaseActivity) -> dict:
    """Convert a BaseActivity ORM object to a plain dict for JSON serialisation."""
    pace_min_per_km = None
    if (
        act.distance_meters
        and float(act.distance_meters) > 0
        and act.moving_duration_seconds
    ):
        pace_sec = float(act.moving_duration_seconds) / (
            float(act.distance_meters) / 1000
        )
        pace_min_per_km = round(pace_sec / 60, 2)

    return {
        "id": act.id,
        "activity_name": act.activity_name,
        "sport_type": act.sport_type_raw,
        "source_type": act.source_type,
        "start_time_local": (
            act.start_time_local.isoformat() if act.start_time_local else None
        ),
        "start_time_gmt": (
            act.start_time_gmt.isoformat() if act.start_time_gmt else None
        ),
        "distance_km": (
            round(float(act.distance_meters) / 1000, 2) if act.distance_meters else 0
        ),
        "distance_meters": float(act.distance_meters) if act.distance_meters else 0,
        "duration_hours": (
            round(float(act.duration_seconds) / 3600, 2) if act.duration_seconds else 0
        ),
        "duration_seconds": float(act.duration_seconds) if act.duration_seconds else 0,
        "moving_duration_seconds": (
            float(act.moving_duration_seconds) if act.moving_duration_seconds else 0
        ),
        "average_hr": act.average_hr,
        "max_hr": act.max_hr,
        "average_cadence": act.average_cadence,
        "average_speed_mps": float(act.average_speed) if act.average_speed else None,
        "pace_min_per_km": pace_min_per_km,
        "calories": float(act.calories) if act.calories else 0,
        "elevation_gain_m": float(act.elevation_gain) if act.elevation_gain else 0,
        "elevation_loss_m": float(act.elevation_loss) if act.elevation_loss else 0,
        "location_name": act.location_name,
        "device_id": act.device_id,
    }


def get_activities(
    user_id: int,
    start_date: str,
    end_date: str,
    sport_types: Optional[str] = None,
    page_size: int = 50,
    page_count: int = 1,
) -> dict:
    """
    Query activity records for a user within a date range.

    Parameters
    ----------
    user_id : int
        The user's ID.
    start_date : str
        Start date in YYYY-MM-DD format (inclusive).
    end_date : str
        End date in YYYY-MM-DD format (inclusive).
    sport_types : str, optional
        Comma-separated sport type filter, e.g. "running,cycling,swimming".
        When omitted, all sport types are included.
    page_size : int
        Number of records per page (default 50).
    page_count : int
        Page number (1-based, default 1).

    Returns
    -------
    dict with keys: status, data (list of activities), total (int), page_count, page_size
    """
    db: Session = SessionLocal()
    try:
        query = (
            db.query(BaseActivity)
            .join(BaseConnect, BaseActivity.base_connect_id == BaseConnect.id)
            .filter(
                BaseActivity.user_id == user_id,
                BaseActivity.start_time_local >= start_date,
                BaseActivity.start_time_local <= end_date,
                BaseConnect.master == True,
            )
        )

        # Sport type filter — optional, applies only when provided
        if sport_types:
            keys = [t.strip() for t in sport_types.split(",")]
            # Also resolve numeric keys to names so both "100" and "running" work
            name_map = {item["key"]: item["name"] for item in ACTIVITY_CONFIG}
            resolved = set(keys)
            for k in keys:
                if k in name_map:
                    resolved.add(name_map[k])
            query = query.filter(BaseActivity.sport_type_raw.in_(list(resolved)))

        total = query.count()
        activities = (
            query.order_by(desc(BaseActivity.start_time_local))
            .limit(page_size)
            .offset((page_count - 1) * page_size)
            .all()
        )

        return {
            "status": "success",
            "data": [_activity_to_dict(a) for a in activities],
            "total": total,
            "page_count": page_count,
            "page_size": page_size,
        }
    finally:
        db.close()
