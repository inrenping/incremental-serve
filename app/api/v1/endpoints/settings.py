from datetime import datetime, timezone, timedelta
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
from app.models.sync_temp import SyncTemp
from app.models.sync_task import SyncTask
from app.core.security import get_current_user
from app.api.v1.endpoints.garmin import (
    save_all_activities as sync_garmin,
    save_new_activities as sync_new_garmin,
    download_garmin_activity as download_garmin,
)
from app.api.v1.endpoints.coros import (
    save_all_activities as sync_coros,
    save_new_coros_activities as sync_new_coros,
    download_coros_activity as download_coros,
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
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    获取当前用户的第三方 App 授权状态（Garmin, Garmin CN, Coros）。
    """
    # 1. 获取 Garmin 配置
    garmin_configs = (
        db.query(GarminConnect)
        .filter(GarminConnect.user_id == current_user.user_id)
        .all()
    )

    garmin_global = next((c for c in garmin_configs if c.region == "GLOBAL"), None)
    garmin_cn = next((c for c in garmin_configs if c.region == "CN"), None)

    # 2. 获取 Coros 配置
    coros_config = (
        db.query(CorosConnect)
        .filter(CorosConnect.user_id == current_user.user_id)
        .first()
    )

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
        garmin_item.update(
            {
                "email": garmin_global.garmin_account,
                "addedAt": format_datetime(garmin_global.created_at),
                "status": "验证通过" if garmin_global.is_active else "已失效",
                "region": "国际区",
                "lastUpdate": format_datetime(garmin_global.updated_at),
            }
        )
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
        garmin_cn_item.update(
            {
                "email": garmin_cn.garmin_account,
                "addedAt": format_datetime(garmin_cn.created_at),
                "status": "验证通过" if garmin_cn.is_active else "已失效",
                "region": "中国区",
                "lastUpdate": format_datetime(garmin_cn.updated_at),
            }
        )
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
        coros_item.update(
            {
                "email": coros_config.coros_account,
                "addedAt": format_datetime(coros_config.created_at),
                "status": "验证通过" if coros_config.is_active else "已失效",
                "region": coros_config.region,
                "lastUpdate": format_datetime(coros_config.updated_at),
                "total_count": coros_config.total_count,
            }
        )
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
    db: Session = Depends(get_db),
):
    """分页获取运动记录，支持不同平台查询。"""

    if platform in ["garmin", "garmin_cn"]:
        region_filter = "GLOBAL" if platform == "garmin" else "CN"

        # 获取该用户的 Garmin 授权配置，避免联合查询
        config = (
            db.query(GarminConnect)
            .filter(
                GarminConnect.user_id == current_user.user_id,
                GarminConnect.region == region_filter,
            )
            .first()
        )

        if not config:
            return {"status": "success", "data": [], "total": 0}

        query = db.query(GarminActivity).filter(
            GarminActivity.garmin_connect_id == config.id
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
            data.append(
                {
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
                    "syncTime": format_datetime(activity.updated_at),
                }
            )

        return {"status": "success", "data": data, "total": total}
    elif platform == "coros":
        query = db.query(CorosActivity).filter(
            CorosActivity.user_id == current_user.user_id
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
            data.append(
                {
                    "id": activity.id,
                    "title": activity.name,
                    "startTime": activity.start_time,
                    "type": str(activity.sport_type),
                    "workoutTime": format_duration(activity.workout_time),
                    "totalTime": format_duration(activity.total_time),
                    "distance": f"{float(activity.distance or 0) / 1000:.2f} km",
                    "elevation": f"{(activity.ascent or 0)} m",
                    "platform": "Coros",
                    "platformId": activity.label_id,
                    "syncTime": activity.updated_at,
                }
            )

        return {"status": "success", "data": data, "total": total}
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")


@router.post("/syncAllActivities")
def sync_all_activities(
    platform: str = "garmin",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
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
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    同步所有已连接平台的最新运动记录。
    此接口会尝试为用户绑定的所有活跃平台（Garmin Global, Garmin CN, Coros）触发增量同步。
    """
    results = {}

    # 1. 获取并处理 Garmin 配置 (可能包含多个区域)
    garmin_configs = (
        db.query(GarminConnect)
        .filter(
            GarminConnect.user_id == current_user.user_id,
            GarminConnect.is_active == True,
        )
        .all()
    )

    # 2. 获取并处理 Coros 配置
    coros_auth = (
        db.query(CorosConnect)
        .filter(
            CorosConnect.user_id == current_user.user_id, CorosConnect.is_active == True
        )
        .first()
    )

    # 校验：检查用户是否绑定了运动平台
    # 这里判断逻辑为：如果当前没有任何活跃的平台连接，则提示用户先绑定
    active_platforms_count = len(garmin_configs) + (1 if coros_auth else 0)
    if active_platforms_count < 2:
        raise HTTPException(
            status_code=400,
            detail="请先前往设置页面绑定 Garmin 或 Coros 账号，再进行同步。",
        )

    for config in garmin_configs:
        platform_key = f"garmin_{config.region.lower()}"
        try:
            # 调用 Garmin 增量同步
            res = sync_new_garmin(
                region=config.region, current_user=current_user, db=db
            )
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

    return {"status": "success", "results": results}


@router.get("/downloadActivity")
def download_activity(
    id: int,
    platform: str = "coros",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    通用运动记录下载接口。
    根据 platform 参数分发到对应的平台下载逻辑。
    """
    if platform.lower() == "coros":
        return download_coros(id=id, current_user=current_user, db=db)
    return download_garmin(id=id, current_user=current_user, db=db)

@router.get("/downloadUploadActivity")
def download_upload_activity(
    id: int,
    platform: str,
    target_platform: str ,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    通用运动记录下载接口。
    根据 platform 参数分发到对应的平台下载逻辑。
    """
    if platform.lower() == "coros":
        return download_coros(id=id, current_user=current_user, db=db)
    return download_garmin(id=id, current_user=current_user, db=db)


@router.get("/generateSyncTask")
def download_activity(
    total_count: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    # 获取三个平台的数据
    garmin_activities = []
    garmin_cn_activities = []
    coros_activities = []

    garmin_activities = db.query(GarminActivity).filter(
            GarminActivity.user_id == current_user.user_id,
            GarminActivity.region == "GLOBAL",
        ).limit(total_count).all()

    garmin_cn_activities = db.query(GarminActivity).filter(
            GarminActivity.user_id == current_user.user_id,
            GarminActivity.region == "CN",
        ).limit(total_count).all()

    coros_activities = db.query(CorosActivity).filter(
            CorosActivity.user_id == current_user.user_id
        ).limit(total_count).all()

    # 获取 batchId,生成 batchId
    max_batch_row = (
        db.query(SyncTemp.batch_id)
        .filter(
            SyncTemp.user_id == current_user.user_id,
            SyncTemp.batch_id.isnot(None),
        )
        .order_by(desc(SyncTemp.batch_id))
        .first()
    )
    batchId = 1 if not max_batch_row else max_batch_row[0] + 1

    # 往临时表插入 Garmin 国际版的数据
    if len(garmin_activities) > 0:
        for activity in garmin_activities:
            db.add(
                SyncTemp(
                    user_id=current_user.user_id,
                    batch_id=batchId,
                    ga_id=activity.id,
                    start_time=activity.start_time,
                    distance=activity.distance,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
    db.commit()            

    # 往临时表插入 佳明中国版的数据（比对数据后插入）
    if len(garmin_cn_activities) > 0:
        sync_temp_list = db.query(SyncTemp).filter(
            SyncTemp.user_id == current_user.user_id,
            SyncTemp.batch_id == batchId,
        ).all()

        for activity in garmin_cn_activities:
            matched = False
            for sync_temp in sync_temp_list:
                if sync_temp.ga_id is not None:
                    # 判断是否是同一个活动
                    if is_same_activity(
                        activity.start_time,
                        activity.distance,
                        sync_temp.start_time,
                        sync_temp.distance,
                    ):
                        sync_temp.gac_id = activity.id
                        sync_temp.updated_at = datetime.now(timezone.utc)
                        matched = True
                        break

            if not matched:
                db.add(
                    SyncTemp(
                        user_id=current_user.user_id,
                        batch_id=batchId,
                        gac_id=activity.id,
                        start_time=activity.start_time,
                        distance=activity.distance,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
    db.commit()

    # 往临时表插入 高驰的数据
    if len(coros_activities) > 0:
        sync_temp_list = db.query(SyncTemp).filter(
            SyncTemp.user_id == current_user.user_id,
            SyncTemp.batch_id == batchId,
        ).all()

        for activity in coros_activities:
            matched = False
            for sync_temp in sync_temp_list:
                if sync_temp.ga_id is not None:
                    # 判断是否是同一个活动
                    if is_same_activity(
                        activity.start_time,
                        activity.distance,
                        sync_temp.start_time,
                        sync_temp.distance,
                    ):
                        sync_temp.ca_id = activity.id
                        sync_temp.updated_at = datetime.now(timezone.utc)
                        matched = True
                        break

            if not matched:
                db.add(
                    SyncTemp(
                        user_id=current_user.user_id,
                        batch_id=batchId,
                        ca_id=activity.id,
                        start_time=activity.start_time,
                        distance=activity.distance,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
    db.commit()

    # 根据临时表生成同步任务
    sync_temp_list = db.query(SyncTemp).filter(
        SyncTemp.user_id == current_user.user_id,
        SyncTemp.batch_id == batchId,
    ).all()
    for sync_temp in sync_temp_list:
        ga_id = sync_temp.ga_id
        gac_id = sync_temp.gac_id
        ca_id = sync_temp.ca_id

        present_count = sum(x is not None for x in (ga_id, gac_id, ca_id))

        # 全空 / 全满：不生成任务
        if present_count == 0 or present_count == 3:
            continue

        # 两空一有：由唯一已有平台同步到另外两个平台
        if present_count == 1:
            if ga_id is not None:
                add_sync_task(db, current_user.user_id, "GLOBAL", ga_id, "CN")
                add_sync_task(db, current_user.user_id, "GLOBAL", ga_id, "Coros")
            elif gac_id is not None:
                add_sync_task(db, current_user.user_id, "CN", gac_id, "GLOBAL")
                add_sync_task(db, current_user.user_id, "CN", gac_id, "Coros")
            else:
                add_sync_task(db, current_user.user_id, "Coros", ca_id, "GLOBAL")
                add_sync_task(db, current_user.user_id, "Coros", ca_id, "CN")
            continue

        # 一空两有：补齐缺失平台
        if ga_id is None:
            # 优先使用国内平台作为源，其次 Coros
            if gac_id is not None:
                add_sync_task(db, current_user.user_id, "CN", gac_id, "GLOBAL")
            else:
                add_sync_task(db, current_user.user_id, "Coros", ca_id, "GLOBAL")
        elif gac_id is None:
            # 优先使用全球平台作为源，其次 Coros
            if ga_id is not None:
                add_sync_task(db, current_user.user_id, "GLOBAL", ga_id, "CN")
            else:
                add_sync_task(db, current_user.user_id, "Coros", ca_id, "CN")
        else:
            # ca_id is None：优先使用国内平台作为源，其次全球平台
            if gac_id is not None:
                add_sync_task(db, current_user.user_id, "CN", gac_id, "Coros")
            else:
                add_sync_task(db, current_user.user_id, "GLOBAL", ga_id, "Coros")

    db.commit()

    sync_task_list = db.query(SyncTask).filter(
        SyncTask.user_id == current_user.user_id,
        SyncTask.batch_id == batchId,
    ).all()
    # for sync_task in sync_task_list:
            

    return {"status": "success", "batchId": batchId}

def is_same_activity(
    source_start_time: Optional[datetime],
    source_distance: Optional[float],
    target_start_time: Optional[datetime],
    target_distance: Optional[float],
) -> bool:
    """
    判断两条活动记录是否可视为同一条活动。

    判定规则：
    1. 起始时间和距离任一为空时，直接返回 False。
    2. 起始时间差超过 5 分钟，返回 False。
    3. 距离差以目标距离为基准，允许 5% 误差；在误差范围内返回 True。

    Args:
        source_start_time: 源平台活动开始时间。
        source_distance: 源平台活动距离。
        target_start_time: 目标平台活动开始时间。
        target_distance: 目标平台活动距离（用于计算容差基准）。

    Returns:
        bool: True 表示两条记录可判定为同一活动，False 表示不是。
    """
    if (
        source_start_time is None
        or target_start_time is None
        or source_distance is None
        or target_distance is None
    ):
        return False

    time_diff_seconds = abs((source_start_time - target_start_time).total_seconds())
    if time_diff_seconds > 5 * 60:
        return False

    # 以目标距离为基准，允许 5% 误差
    distance_tolerance = abs(target_distance) * 0.05
    distance_diff = abs(source_distance - target_distance)
    return distance_diff <= distance_tolerance


def add_sync_task(
    db: Session,
    user_id: int,
    source_platform: str,
    source_id: int,
    target_platform: str,
) -> None:
    """
    向同步任务表新增一条待处理任务（幂等）。

    行为说明：
    1. 当 source_id 为空时，不创建任务。
    2. 若同一用户在同一源平台下，source_id 已存在任务记录，则跳过，避免重复。
    3. 若不存在，则插入一条默认待处理任务（sync_status = -1）。

    Args:
        db: 数据库会话。
        user_id: 用户 ID。
        source_platform: 源平台标识（如 GLOBAL/CN/Coros）。
        source_id: 源平台活动 ID。
        target_platform: 目标平台标识。
    """
    if source_id is None:
        return

    exists = db.query(SyncTask.id).filter(
        SyncTask.user_id == user_id,
        SyncTask.source_platform == source_platform,
        SyncTask.source_id == source_id,
    ).first()
    if exists:
        return

    db.add(
        SyncTask(
            user_id=user_id,
            source_platform=source_platform,
            source_id=source_id,
            target_platform=target_platform,
            target_id=0,
            sync_status=-1,
            created_at=datetime.now(timezone.utc),
        )
    )


@router.delete("/deleteTemp")
def delete_activity(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # 删除当前用户 24 小时之前的临时同步数据
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=1)
    deleted_count = (
        db.query(SyncTemp)
        .filter(
            SyncTemp.user_id == current_user.user_id,
            SyncTemp.created_at.isnot(None),
            SyncTemp.created_at < cutoff_time,
        )
        .delete(synchronize_session=False)
    )
    db.commit()

    return {"status": "success", "deletedCount": deleted_count}
