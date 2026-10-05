from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc, extract

from app.db.session import get_db
from app.models.base_connect import BaseConnect
from app.models.main_activity import MainActivity
from app.models.user import User
from app.core.security import get_current_user
from app.services import main_activity_service
from app.utils.activity_type_config import ACTIVITY_CONFIG

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
    connect_id: Optional[int] = None,
    page_size: int = 10,
    page_count: int = 1,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sport_types: Optional[str] = None,
    name: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分页查询主数据源运动记录（t_main_activity）。

    过滤参数与 /base/getActivitiesByPage 保持一致，前端图表可以直接换 URL 切换数据源。
    connect_id 省略时返回该用户所有连接的主表记录。
    """
    query = db.query(MainActivity).filter(MainActivity.user_id == current_user.id)

    # 1. 按连接过滤（与 base 版本一致：连接不存在或不属于当前用户时返回空）
    if connect_id:
        base_connect = (
            db.query(BaseConnect)
            .filter(BaseConnect.id == connect_id, BaseConnect.user_id == current_user.id)
            .first()
        )
        if not base_connect:
            return {"status": "success", "data": [], "total": 0}
        query = query.filter(MainActivity.base_connect_id == connect_id)

    # 2. 时间区间
    if start_date:
        query = query.filter(MainActivity.start_time_local >= start_date)
    if end_date:
        query = query.filter(MainActivity.start_time_local <= end_date)

    # 3. 运动类型（支持多选，逗号分隔：既有原始 key 也有展开后的 name）
    if sport_types:
        key_list = [t.strip() for t in sport_types.split(",")]
        key_list.extend(
            [item["name"] for item in ACTIVITY_CONFIG if item["key"] in key_list]
        )
        query = query.filter(MainActivity.sport_type_raw.in_(key_list))

    # 4. 名称模糊搜索
    if name:
        query = query.filter(MainActivity.activity_name.ilike(f"%{name}%"))

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
