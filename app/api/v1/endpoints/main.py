from datetime import datetime

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
