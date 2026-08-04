from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc, extract

from app.db.session import get_db
from app.models.main_activity import MainActivity
from app.models.user import User
from app.core.security import get_current_user
from app.services import main_activity_service

router = APIRouter()


@router.get("/syncBaseToMainActivity")
def sync_base_to_main_activity(
    db: Session = Depends(get_db),
):
    """
    将 t_base_activity 中主数据源的数据同步到 t_main_activity。

    规则：
    1. 只同步 t_base_connect.master=True 的数据
    2. 已存在的 activity_id 会跳过
    3. id 使用新表的自增主键
    """
    return main_activity_service.sync_base_to_main_activity(db)


@router.get("/getActivitiesByPage")
def get_activities_by_page(
    page_size: int = 10,
    page_count: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    分页获取主数据源的运动记录
    """
    query = db.query(MainActivity).filter(
        MainActivity.user_id == current_user.id,
    )

    total = query.count()

    result = (
        query.order_by(desc(MainActivity.start_time_local))
        .limit(page_size)
        .offset((page_count - 1) * page_size)
        .all()
    )

    return {"status": "success", "data": result, "total": total}


@router.get("/getActivitiesByMonth")
def get_activities_by_month(
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    根据年月获取当月全部运动记录
    """
    result = (
        db.query(MainActivity)
        .filter(
            MainActivity.user_id == current_user.id,
            extract("year", MainActivity.start_time_local) == year,
            extract("month", MainActivity.start_time_local) == month,
        )
        .order_by(desc(MainActivity.start_time_local))
        .all()
    )

    return {"status": "success", "data": result, "total": len(result)}


@router.get("/getActivitiesByWeek")
def get_activities_by_week(
    date: str = datetime.now().strftime("%Y-%m-%d"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    根据日期获取最近 6 周（含该日期所在周）的全部运动记录。

    周以周一为起始，从该日期所在周向前取 5 周共 6 周，
    避免按月统计时跨周的周初/周末数据被截断。
    """
    target = datetime.strptime(date, "%Y-%m-%d")
    monday = target - timedelta(days=target.weekday())  # 该日期所在周的周一
    range_start = monday - timedelta(weeks=5)  # 共 6 周
    range_end = monday + timedelta(days=6)  # 该周的周日

    result = (
        db.query(MainActivity)
        .filter(
            MainActivity.user_id == current_user.id,
            MainActivity.start_time_local >= range_start,
            MainActivity.start_time_local < range_end + timedelta(days=1),
        )
        .order_by(desc(MainActivity.start_time_local))
        .all()
    )

    return {"status": "success", "data": result, "total": len(result)}
