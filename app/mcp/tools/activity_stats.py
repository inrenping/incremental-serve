"""
MCP Tool: get_activity_stats

Aggregate activity statistics for a user within a given time period,
grouped by day, week, or month. Supports optional sport type filtering.
"""

from typing import Optional

from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.utils.activity_type_config import ACTIVITY_CONFIG


def _get_group_expr(group_by: str):
    """Return SQLAlchemy expression(s) for GROUP BY clause."""
    if group_by == "day":
        return [
            extract("year", BaseActivity.start_time_local).label("year"),
            extract("month", BaseActivity.start_time_local).label("month"),
            extract("day", BaseActivity.start_time_local).label("day"),
        ]
    elif group_by == "week":
        return [
            extract("year", BaseActivity.start_time_local).label("year"),
            extract("week", BaseActivity.start_time_local).label("week"),
        ]
    else:  # month
        return [
            extract("year", BaseActivity.start_time_local).label("year"),
            extract("month", BaseActivity.start_time_local).label("month"),
        ]


def _group_label(group_by: str, row) -> str:
    """Build a human-readable group label, e.g. '2026-07' or '2026-W27'."""
    if group_by == "day":
        return f"{int(row.year)}-{int(row.month):02d}-{int(row.day):02d}"
    elif group_by == "week":
        return f"{int(row.year)}-W{int(row.week):02d}"
    else:
        return f"{int(row.year)}-{int(row.month):02d}"


def get_activity_stats(
    user_id: int,
    start_date: str,
    end_date: str,
    group_by: str = "month",
    sport_types: Optional[str] = None,
) -> dict:
    """
    Get aggregated activity statistics for a user within a date range.

    Parameters
    ----------
    user_id : int
        The user's ID.
    start_date : str
        Start date in YYYY-MM-DD format (inclusive).
    end_date : str
        End date in YYYY-MM-DD format (inclusive).
    group_by : str
        Aggregation granularity: "day", "week", or "month" (default "month").
    sport_types : str, optional
        Comma-separated sport type filter, e.g. "running,cycling".
        When omitted, all sport types are included.

    Returns
    -------
    dict with keys:
      - status: str
      - data: list of dicts with period, total_distance_km, total_duration_h,
              activity_count, avg_pace_min_per_km, avg_heart_rate, total_calories,
              total_elevation_gain_m
      - overall: dict with overall aggregates for the whole period
    """
    if group_by not in ("day", "week", "month"):
        group_by = "month"

    db: Session = SessionLocal()
    try:
        group_exprs = _get_group_expr(group_by)

        query = (
            db.query(
                *group_exprs,
                func.sum(BaseActivity.distance_meters).label("total_distance"),
                func.sum(BaseActivity.duration_seconds).label("total_duration"),
                func.count(BaseActivity.id).label("activity_count"),
                func.avg(BaseActivity.average_hr).label("avg_heart_rate"),
                func.sum(BaseActivity.calories).label("total_calories"),
                func.sum(BaseActivity.elevation_gain).label("total_elevation_gain"),
                func.sum(BaseActivity.moving_duration_seconds).label(
                    "total_moving_duration"
                ),
            )
            .join(BaseConnect, BaseActivity.base_connect_id == BaseConnect.id)
            .filter(
                BaseActivity.user_id == user_id,
                BaseActivity.start_time_local >= start_date,
                BaseActivity.start_time_local <= end_date,
                BaseConnect.master == True,
            )
        )

        # Sport type filter — optional
        if sport_types:
            keys = [t.strip() for t in sport_types.split(",")]
            name_map = {item["key"]: item["name"] for item in ACTIVITY_CONFIG}
            resolved = set(keys)
            for k in keys:
                if k in name_map:
                    resolved.add(name_map[k])
            query = query.filter(BaseActivity.sport_type_raw.in_(list(resolved)))

        rows = query.group_by(*group_exprs).order_by(*group_exprs).all()

        data = []
        overall = {
            "total_distance_km": 0.0,
            "total_duration_h": 0.0,
            "activity_count": 0,
            "total_calories": 0.0,
            "total_elevation_gain_m": 0.0,
            "avg_pace_min_per_km": None,
        }

        for row in rows:
            dist_km = round(float(row.total_distance or 0) / 1000, 2)
            dur_h = round(float(row.total_duration or 0) / 3600, 2)
            moving_sec = float(row.total_moving_duration or 0)

            pace = None
            if dist_km > 0 and moving_sec > 0:
                pace = round(moving_sec / 60 / dist_km, 2)

            entry = {
                "period": _group_label(group_by, row),
                "total_distance_km": dist_km,
                "total_duration_h": dur_h,
                "activity_count": int(row.activity_count),
                "avg_pace_min_per_km": pace,
                "avg_heart_rate": (
                    round(float(row.avg_heart_rate), 1) if row.avg_heart_rate else None
                ),
                "total_calories": round(float(row.total_calories or 0), 0),
                "total_elevation_gain_m": round(
                    float(row.total_elevation_gain or 0), 1
                ),
            }
            data.append(entry)

            # Accumulate overall
            overall["total_distance_km"] += dist_km
            overall["total_duration_h"] += dur_h
            overall["activity_count"] += int(row.activity_count)
            overall["total_calories"] += float(row.total_calories or 0)
            overall["total_elevation_gain_m"] += float(row.total_elevation_gain or 0)

        overall["total_distance_km"] = round(overall["total_distance_km"], 2)
        overall["total_duration_h"] = round(overall["total_duration_h"], 2)
        overall["total_calories"] = round(overall["total_calories"], 0)
        overall["total_elevation_gain_m"] = round(overall["total_elevation_gain_m"], 1)

        # Average pace for overall
        total_dist_km = overall["total_distance_km"]
        if total_dist_km > 0 and any(d["total_duration_h"] > 0 for d in data):
            total_moving = sum(
                d["total_duration_h"] * 3600 for d in data if d["total_duration_h"] > 0
            )
            if total_moving > 0:
                overall["avg_pace_min_per_km"] = round(
                    total_moving / 60 / total_dist_km, 2
                )

        return {
            "status": "success",
            "data": data,
            "overall": overall,
        }
    finally:
        db.close()
