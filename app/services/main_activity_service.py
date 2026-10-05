from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.main_activity import MainActivity


def sync_base_to_main_activity(db: Session) -> dict:
    """
    将 t_base_activity 中主数据源的数据同步到 t_main_activity。

    规则：
    1. 只同步 t_base_connect.master=True 的数据
    2. 如果 t_main_activity 中已存在相同 activity_id，则跳过
    3. id 使用新表的自增主键
    """
    # 获取所有主数据源的连接 ID
    master_connects = (
        db.query(BaseConnect.id)
        .filter(BaseConnect.master == True, BaseConnect.is_active == True)
        .all()
    )
    master_connect_ids = [c.id for c in master_connects]

    if not master_connect_ids:
        return {
            "status": "success",
            "message": "没有找到主数据源连接",
            "data": {"synced": 0},
        }

    # 获取主数据源的所有活动记录
    base_activities = (
        db.query(BaseActivity)
        .filter(BaseActivity.base_connect_id.in_(master_connect_ids))
        .all()
    )

    if not base_activities:
        return {
            "status": "success",
            "message": "没有找到需要同步的活动记录",
            "data": {"synced": 0},
        }

    # 获取 t_main_activity 中已存在的 activity_id
    existing_activity_ids = set(
        row[0] for row in db.query(MainActivity.activity_id).all()
    )

    synced_count = 0

    for activity in base_activities:
        # 检查是否已存在
        if activity.activity_id in existing_activity_ids:
            continue

        # 创建新记录（不设置 id，使用自增）
        new_activity = MainActivity(
            user_id=activity.user_id,
            base_connect_id=activity.base_connect_id,
            source_type=activity.source_type,
            activity_id=activity.activity_id,
            activity_name=activity.activity_name,
            sport_type_raw=activity.sport_type_raw,
            sport_mode_raw=activity.sport_mode_raw,
            start_time_gmt=activity.start_time_gmt,
            start_time_local=activity.start_time_local,
            end_time_gmt=activity.end_time_gmt,
            distance_meters=activity.distance_meters,
            duration_seconds=activity.duration_seconds,
            moving_duration_seconds=activity.moving_duration_seconds,
            calories=activity.calories,
            average_hr=activity.average_hr,
            max_hr=activity.max_hr,
            average_cadence=activity.average_cadence,
            max_cadence=activity.max_cadence,
            average_speed=activity.average_speed,
            max_speed=activity.max_speed,
            start_lat=activity.start_lat,
            start_lon=activity.start_lon,
            location_name=activity.location_name,
            device_id=activity.device_id,
            elevation_gain=activity.elevation_gain,
            elevation_loss=activity.elevation_loss,
            created_at=activity.created_at,
            updated_at=activity.updated_at,
        )
        db.add(new_activity)
        existing_activity_ids.add(activity.activity_id)
        synced_count += 1

    db.commit()

    return {
        "status": "success",
        "message": f"同步完成，成功 {synced_count} 条",
        "data": {
            "synced": synced_count,
        },
    }


def sync_base_to_main_activity_all(db: Session, days: int = 7) -> dict:
    """
    将所有主数据源（t_base_connect.master=true 且 is_active=true）的活动记录同步到
    t_main_activity，供 GitHub Actions 等定时任务全量调用。

    与 sync_base_to_main_activity 的区别：
    1. 按 connect 逐个隔离：单个 connect 失败只记进 errors，不影响其他人
    2. 去重键为 (source_type, activity_id)，与 t_main_activity 唯一约束一致
    3. 支持时间窗口：days>0 时只同步 start_time_gmt 在最近 days 天内的记录，
       传 days=0 走全量（首次/手动补数用）
    4. 存在 (source_type, activity_id) 的记录跳过，不存在才插入（不UPDATE已有记录）
    5. 返回 {total, synced, failed, errors}
    """
    master_connects = (
        db.query(BaseConnect)
        .filter(BaseConnect.master == True, BaseConnect.is_active == True)
        .all()
    )

    # 已存在的 (source_type, activity_id) 集合，用于去重
    existing_keys = set(
        (st, aid)
        for st, aid in db.query(MainActivity.source_type, MainActivity.activity_id).all()
    )

    summary = {
        "total": len(master_connects),
        "synced": 0,
        "failed": 0,
        "errors": [],
    }

    if not master_connects:
        return {
            "status": "success",
            "message": "没有找到主数据源连接",
            "data": summary,
        }

    cutoff = None
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for connect in master_connects:
        try:
            query = db.query(BaseActivity).filter(
                BaseActivity.base_connect_id == connect.id
            )
            if cutoff is not None:
                # 带时间窗口时只扫近期记录；start_time_gmt 为空的运动记录一并纳入，避免漏同步
                query = query.filter(
                    (BaseActivity.start_time_gmt >= cutoff)
                    | (BaseActivity.start_time_gmt.is_(None))
                )
            base_activities = query.all()

            for activity in base_activities:
                key = (activity.source_type, activity.activity_id)
                if key in existing_keys:
                    continue

                new_activity = MainActivity(
                    user_id=activity.user_id,
                    base_connect_id=activity.base_connect_id,
                    source_type=activity.source_type,
                    activity_id=activity.activity_id,
                    activity_name=activity.activity_name,
                    sport_type_raw=activity.sport_type_raw,
                    sport_mode_raw=activity.sport_mode_raw,
                    start_time_gmt=activity.start_time_gmt,
                    start_time_local=activity.start_time_local,
                    end_time_gmt=activity.end_time_gmt,
                    distance_meters=activity.distance_meters,
                    duration_seconds=activity.duration_seconds,
                    moving_duration_seconds=activity.moving_duration_seconds,
                    calories=activity.calories,
                    average_hr=activity.average_hr,
                    max_hr=activity.max_hr,
                    average_cadence=activity.average_cadence,
                    max_cadence=activity.max_cadence,
                    average_speed=activity.average_speed,
                    max_speed=activity.max_speed,
                    start_lat=activity.start_lat,
                    start_lon=activity.start_lon,
                    location_name=activity.location_name,
                    device_id=activity.device_id,
                    elevation_gain=activity.elevation_gain,
                    elevation_loss=activity.elevation_loss,
                    created_at=activity.created_at,
                    updated_at=activity.updated_at,
                )
                db.add(new_activity)
                existing_keys.add(key)
                summary["synced"] += 1

            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            summary["failed"] += 1
            summary["errors"].append({"connect_id": connect.id, "reason": str(e)})

    return {
        "status": "success",
        "message": f"同步完成，本次插入 {summary['synced']} 条，失败 {summary['failed']} 个连接",
        "data": summary,
    }

