"""
MCP Tool: get_heart_rate_data

Query daily heart rate summaries and detailed sampling data
for a user within a date range.
"""

from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.heart_rate_daily import HeartRateDaily
from app.models.heart_rate_detail import HeartRateDetail
from app.models.user import User


def get_heart_rate_data(
    user_id: int,
    start_date: str,
    end_date: str,
    include_details: bool = True,
) -> dict:
    """
    Query heart rate data for a user within a date range.

    Parameters
    ----------
    user_id : int
        The user's ID.
    start_date : str
        Start date in YYYY-MM-DD format (inclusive).
    end_date : str
        End date in YYYY-MM-DD format (inclusive).
    include_details : bool, optional
        Whether to include per-minute sampling details (default True).
        Set to False if you only need daily summary metrics.

    Returns
    -------
    dict with keys:
      - status: str
      - data: list of daily heart rate records, each containing:
          - calendar_date: str
          - max_heart_rate: int or None
          - min_heart_rate: int or None
          - resting_heart_rate: int or None
          - last_seven_days_avg_resting_heart_rate: int or None
          - details (if include_details=True): list of {sample_time, heart_rate}
    """
    db: Session = SessionLocal()
    try:
        # Parse date range
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        # Get user for timezone
        user = db.query(User).filter(User.id == user_id).first()
        user_tz = (
            ZoneInfo(user.timezone)
            if user and user.timezone
            else ZoneInfo("Asia/Shanghai")
        )

        # Query daily summaries
        daily_records = (
            db.query(HeartRateDaily)
            .filter(
                HeartRateDaily.user_id == user_id,
                HeartRateDaily.calendar_date >= start,
                HeartRateDaily.calendar_date <= end,
            )
            .order_by(HeartRateDaily.calendar_date)
            .all()
        )

        data = []
        for daily in daily_records:
            entry = {
                "calendar_date": daily.calendar_date.isoformat(),
                "max_heart_rate": daily.max_heart_rate,
                "min_heart_rate": daily.min_heart_rate,
                "resting_heart_rate": daily.resting_heart_rate,
                "last_seven_days_avg_resting_heart_rate": daily.last_seven_days_avg_resting_heart_rate,
            }

            if include_details:
                # Calculate the UTC range for this day in the user's timezone
                start_of_day = datetime.combine(
                    daily.calendar_date, time.min, tzinfo=user_tz
                ).astimezone(timezone.utc)
                end_of_day = datetime.combine(
                    daily.calendar_date, time.max, tzinfo=user_tz
                ).astimezone(timezone.utc)

                details = (
                    db.query(HeartRateDetail)
                    .filter(
                        HeartRateDetail.daily_id == daily.id,
                        HeartRateDetail.sample_time.between(start_of_day, end_of_day),
                    )
                    .order_by(HeartRateDetail.sample_time)
                    .all()
                )

                entry["details"] = [
                    {
                        "sample_time": d.sample_time.isoformat(),
                        "heart_rate": d.heart_rate,
                    }
                    for d in details
                ]

            data.append(entry)

        return {
            "status": "success",
            "data": data,
            "total_days": len(data),
        }
    finally:
        db.close()
