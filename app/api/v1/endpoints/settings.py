from datetime import datetime, timezone, timedelta
from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app import db
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
    pull_full_activities as pull_full_garmin,
    pull_new_activities as pull_new_garmin,
    download_garmin_activity as download_garmin,
    upload_garmin_activity_to_garmin,
    upload_coros_activity_to_garmin,
)
from app.api.v1.endpoints.coros import (
    pull_full_activities as pull_full_coros,
    pull_new_activities as pull_new_coros,
    save_new_coros_activities as sync_new_coros,
    download_coros_activity as download_coros,
    upload_garmin_activity_to_coros,
)

from app.services import garmin_service,coros_service

router = APIRouter()

class OneClickSyncRequest(BaseModel):
    """一键同步请求模型"""
    source: str
    target: str

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

def _format_garmin_activity_dict(activity: GarminActivity, region: str) -> dict:
    """将 Garmin 活动模型转换为前端使用的统一字典格式。"""
    return {
        "id": activity.id,
        "title": activity.activity_name,
        "startTime": activity.start_time_local,
        "type": activity.activity_type_key,
        "workoutTime": format_duration(activity.moving_duration_seconds),
        "totalTime": format_duration(activity.duration_seconds),
        "distance": f"{float(activity.distance_meters or 0) / 1000:.2f} km",
        "elevation": f"{int(activity.elevation_gain or 0)} m",
        "platform": region,
        "platformId": str(activity.activity_id),
        "syncTime": format_datetime(activity.updated_at),
    }

def _format_coros_activity_dict(activity: CorosActivity) -> dict:
    """将 Coros 活动模型转换为前端使用的统一字典格式。"""
    return {
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
        "syncTime": format_datetime(activity.updated_at),
    }

def _build_app_config_item(
    app_id: str, 
    label: str, 
    description: str, 
    config: Optional[Annotated[GarminConnect, CorosConnect]], 
    region_label: Optional[str] = None
) -> dict:
    """统一构建第三方应用授权状态的响应字典。"""
    is_connected = config.is_active if config else False
    item = {
        "id": app_id,
        "label": label,
        "description": description,
        "isConnected": is_connected,
        "total_count": config.total_count if config else 0,
    }
    if config:
        item.update({
            "email": getattr(config, "garmin_account", None) or getattr(config, "coros_account", None),
            "addedAt": format_datetime(config.created_at),
            "status": "验证通过" if is_connected else "已失效",
            "region": region_label or getattr(config, "region", ""),
            "lastUpdate": format_datetime(config.updated_at),
        })
    return item


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

    # 依次构建各平台配置
    results.append(_build_app_config_item("garmin", "Garmin Connect", "连接您的 Garmin Connect 账号", garmin_global, "GLOBAL"))
    results.append(_build_app_config_item("garmin_cn", "Garmin Connect (CN)", "连接您的 Garmin Connect (中国) 账号", garmin_cn, "CN"))
    results.append(_build_app_config_item("coros", "Coros", "连接您的 Coros 账号", coros_config))

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
        
        data = [_format_garmin_activity_dict(a, region_filter) for a in garminActivities]

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
        
        data = [_format_coros_activity_dict(a) for a in corosActivities]

        return {"status": "success", "data": data, "total": total}
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")

@router.get("/getActivity")
def get_activity(
    id: int,
    platform: str = "coros",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取单条运动记录详情，支持不同平台查询。"""

    if platform.lower() == "coros":
        activity = (
            db.query(CorosActivity)
            .filter(
                CorosActivity.user_id == current_user.user_id,
                CorosActivity.id == id,
            )
            .first()
        )
        if not activity:
            raise HTTPException(status_code=404, detail="未找到对应的 Coros 活动记录")
        return {"status": "success", "data": activity}
    else:
        activity = (
            db.query(GarminActivity)
            .filter(GarminActivity.id == id)
            .first()
        )
        if not activity:
            raise HTTPException(status_code=404, detail="未找到对应的 Garmin 活动记录")
        return {"status": "success", "data": activity}

@router.post("/pullFullActivities")
def pull_full_activities(
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
        return pull_full_garmin(region="GLOBAL", current_user=current_user, db=db)
    elif platform == "garmin_cn":
        # 调用 Garmin 中国区同步接口
        return pull_full_garmin(region="CN", current_user=current_user, db=db)
    elif platform == "coros":
        # 调用 Coros 同步接口
        return pull_full_coros(current_user=current_user, db=db)
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")

@router.post("/pullActivities")
def pull_activities(
    platform: str = "garmin",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    同步下载指定平台的运动记录。(增量同步，如果遇到已有数据停止同步)
    根据 platform 参数分别调用 Garmin (Global/CN) 或 Coros 的同步逻辑。
    """
    if platform == "garmin":
        # 调用 Garmin 国际区同步接口
        return pull_new_garmin(region="GLOBAL", current_user=current_user, db=db)
    elif platform == "garmin_cn":
        # 调用 Garmin 中国区同步接口
        return pull_new_garmin(region="CN", current_user=current_user, db=db)
    elif platform == "coros":
        # 调用 Coros 同步接口
        return pull_new_coros(current_user=current_user, db=db)
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")


@router.post("/syncNewActivities")
def sync_new_activities(
    total_count: int = 10,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    同步所有已连接平台的最新运动记录。
    此接口会尝试为用户绑定的所有活跃平台（Garmin Global, Garmin CN, Coros）触发增量同步。
    """
    # print("一键同步开始。")
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

    # TODO 自动刷新认证  

    for config in garmin_configs:
        platform_key = f"garmin_{config.region.lower()}"
        try:
            # 调用 Garmin 增量同步
            res = pull_new_garmin(
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

    generate_sync_task(total_count,current_user=current_user, db=db)

    return {"status": "success", "results": results}

@router.get("/generateSyncTask")
def generate_sync_task(
    total_count: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    智能生成并执行跨平台同步任务。
    
    逻辑流程：
    1. 生成同步任务（基于最近活动的比对）。
    2. 立即执行本次生成的上传任务。
    """
    # print("生成同步任务开始。")
    # Step 1: Generate and store sync tasks (the "picking" part)
    batchId = _create_and_store_sync_tasks(db, current_user.user_id, total_count)

    # Step 2: Execute the generated tasks
    sync_task_list = db.query(SyncTask).filter(
        SyncTask.user_id == current_user.user_id
    ).all()
    for sync_task in sync_task_list:
        try:
            if sync_task.source_platform in ["GLOBAL", "CN"] and sync_task.target_platform in ["GLOBAL", "CN"]:
                upload_garmin_activity_to_garmin(id=sync_task.source_id, current_user=current_user, db=db)               
            elif sync_task.source_platform in ["GLOBAL", "CN"] and sync_task.target_platform == "Coros":
                upload_garmin_activity_to_coros(id=sync_task.source_id, current_user=current_user, db=db)
            elif sync_task.source_platform == "Coros" and sync_task.target_platform in ["GLOBAL", "CN"]:
                upload_coros_activity_to_garmin(id=sync_task.source_id, region=sync_task.target_platform, current_user=current_user, db=db)
            sync_task.sync_status = 1
            sync_task.synced_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:
            # Log the error but continue processing other tasks
            print(f"Failed to execute sync task {sync_task.id}: {str(e)}")

    return {"status": "success", "batchId": batchId}


def _match_and_update_sync_temp(
    db: Session,
    user_id: int,
    batch_id: int,
    activities: list,
    id_field: str,
):
    """
    内部辅助方法：将给定活动列表与现有的临时同步记录进行比对并更新或新增。
    支持 Garmin (start_time_local) 和 Coros (start_time) 活动模型。
    """
    sync_temp_list = db.query(SyncTemp).filter(
        SyncTemp.user_id == user_id,
        SyncTemp.batch_id == batch_id,
    ).all()
   
    for activity in activities:
        matched = False
        # 先尝试获取时间字段，如果都没有则赋值 None
        start_time = getattr(activity, "start_time_local", None)
        if start_time is None:
            start_time = getattr(activity, "start_time", None)
        
        distance = getattr(activity, "distance_meters", None) or getattr(activity, "distance", 0)

        # 只有在 start_time 不为 None 的情况下才加时区
        if start_time is not None and start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        for sync_temp in sync_temp_list:
            sync_time = sync_temp.start_time
            if sync_time is not None and sync_time.tzinfo is None:
                sync_time = sync_time.replace(tzinfo=timezone.utc)
            
            # print(f"比对时间: {start_time},{sync_time}")
            # print(f"比对活动: {activity.id} 与临时记录: {sync_temp.id}，是否相同: {is_same_activity(start_time, sync_time)}")
            if is_same_activity(start_time, sync_time):
                setattr(sync_temp, id_field, activity.id)
                sync_temp.updated_at = datetime.now(timezone.utc)
                matched = True
                break

    if not matched:
        new_temp = SyncTemp(user_id=user_id, batch_id=batch_id, start_time=start_time, distance=distance)
        setattr(new_temp, id_field, activity.id)
        db.add(new_temp)


def _create_and_store_sync_tasks(
    db: Session, user_id: int, total_count: int
) -> int:
    """
    Helper function to generate and store sync tasks based on recent activities.
    Returns the batchId of the created tasks.
    """
    # 1. 获取三个平台的数据
    garmin_activities = db.query(GarminActivity).join(GarminConnect).filter(
            GarminActivity.user_id == user_id,
            GarminConnect.region == "GLOBAL",
        ).order_by(desc(GarminActivity.start_time_gmt)).limit(total_count).all()

    garmin_cn_activities = db.query(GarminActivity).join(GarminConnect).filter(
            GarminActivity.user_id == user_id,
            GarminConnect.region == "CN",
        ).order_by(desc(GarminActivity.start_time_gmt)).limit(total_count).all()

    coros_activities = db.query(CorosActivity).filter(
            CorosActivity.user_id == user_id
        ).order_by(desc(CorosActivity.start_time)).limit(total_count).all()

    # 2. 生成唯一的批次 ID (batchId)
    max_batch_row = (
        db.query(SyncTemp.batch_id)
        .filter(
            SyncTemp.user_id == user_id,
            SyncTemp.batch_id.isnot(None),
        )
        .order_by(desc(SyncTemp.batch_id))
        .first()
    )
    batchId = 1 if not max_batch_row else max_batch_row[0] + 1

    # 3. 往临时表插入 Garmin 国际版的数据作为基础记录
    if len(garmin_activities) > 0:
        for activity in garmin_activities:
            db.add(
                SyncTemp(
                    user_id=user_id,
                    batch_id=batchId,
                    ga_id=activity.id,
                    start_time=activity.start_time_local,
                    distance=activity.distance_meters,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
    db.commit()

    # 4. 往临时表插入 佳明中国版的数据（执行比对，匹配则更新，不匹配则新增）
    if garmin_cn_activities:
        _match_and_update_sync_temp(db, user_id, batchId, garmin_cn_activities, "gac_id")
    db.commit()

    # 5. 往临时表插入 高驰的数据（同上，执行跨平台匹配）
    if coros_activities:
        _match_and_update_sync_temp(db, user_id, batchId, coros_activities, "ca_id")
    db.commit()

    # 6. 分析比对结果，根据缺失情况生成同步任务 (SyncTask)
    sync_temp_list = db.query(SyncTemp).filter(
        SyncTemp.user_id == user_id,
        SyncTemp.batch_id == batchId,
    ).all()

    add_sync_task_list = []
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
                add_sync_task_list.append(
                  add_sync_task(db, user_id, "GLOBAL", ga_id, "CN")
                )
                add_sync_task_list.append(
                  add_sync_task(db, user_id, "GLOBAL", ga_id, "Coros")
                )
            elif gac_id is not None:
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "CN", gac_id, "GLOBAL")
                )
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "CN", gac_id, "Coros")
                ) 
            else: # ca_id is not None
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "Coros", ca_id, "GLOBAL")
                )
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "Coros", ca_id, "CN")
                )
            continue

        # 一空两有：补齐缺失平台
        if ga_id is None:
            # 优先使用国内平台作为源，其次 Coros
            if gac_id is not None:
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "CN", gac_id, "GLOBAL")
                )
            else: # ca_id must be present
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "Coros", ca_id, "GLOBAL")
                )
        elif gac_id is None:
            # 优先使用全球平台作为源，其次 Coros
            if ga_id is not None:
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "GLOBAL", ga_id, "CN")
                )
            else: # ca_id must be present
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "Coros", ca_id, "CN")
                )
        else: # ca_id is None：优先使用国内平台作为源，其次全球平台
            if gac_id is not None:
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "CN", gac_id, "Coros")
                )
            else: # ga_id must be present
                add_sync_task_list.append(
                    add_sync_task(db, user_id, "GLOBAL", ga_id, "Coros")
                )
    # print(f"本次生成的同步任务数量: {len(add_sync_task_list)}")
    db.add_all([t for t in add_sync_task_list if t is not None])    
    db.commit() 
    return batchId


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
    if (
        source_start_time is None
        or target_start_time is None
    ):
        return False

    time_diff_seconds = abs((source_start_time - target_start_time).total_seconds())
    if time_diff_seconds > 5 * 60:
        return False
    return True

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

    return SyncTask(
            user_id=user_id,
            source_platform=source_platform,
            source_id=source_id,
            target_platform=target_platform,
            target_id=0,
            sync_status=-1,
            created_at=datetime.now(timezone.utc),
        )
    

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

@router.delete("/deleteTemp")
def delete_temp(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    清理过期的临时比对数据。
    为了节省数据库空间，定期删除 24 小时之前的 SyncTemp 记录。
    """
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

@router.post("/oneclickSyncActivities")
def one_click_sync_activities(
    payload: OneClickSyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    一键同步指定源平台到目标平台。
    由前端传入 source (源平台) 和 target (目标平台) 标识。
    """
    if payload.source == payload.target:
        raise HTTPException(status_code=400, detail="源平台和目标平台不能相同")

    # 1. 刷新相关平台数据
    requested_platforms = {payload.source, payload.target}
    if "garmin" in requested_platforms:
        pull_new_garmin(region="GLOBAL", current_user=current_user, db=db)
    if "garmin_cn" in requested_platforms:
        pull_new_garmin(region="CN", current_user=current_user, db=db)
    if "coros" in requested_platforms:
        pull_new_coros(current_user=current_user, db=db)
    
    total_count = 10

    def _get_activities(platform: str):
        """辅助获取指定平台的最近运动记录"""
        if platform == "garmin":
            return db.query(GarminActivity).join(GarminConnect).filter(
                GarminActivity.user_id == current_user.user_id,
                GarminConnect.region == "GLOBAL",
            ).order_by(desc(GarminActivity.start_time_gmt)).limit(total_count).all()
        elif platform == "garmin_cn":
            return db.query(GarminActivity).join(GarminConnect).filter(
                GarminActivity.user_id == current_user.user_id,
                GarminConnect.region == "CN",
            ).order_by(desc(GarminActivity.start_time_gmt)).limit(total_count).all()
        elif platform == "coros":
            return db.query(CorosActivity).filter(
                CorosActivity.user_id == current_user.user_id
            ).order_by(desc(CorosActivity.start_time)).limit(total_count).all()
        return []

    # 2. 执行双向推送：p1 -> p2 和 p2 -> p1
    p1, p2 = payload.source, payload.target
    for src, dst in [(p1, p2), (p2, p1)]:
        activities = _get_activities(src)
        for act in activities:
            try:
                # 佳明内部跨区同步 (CN <-> GLOBAL)
                if src.startswith("garmin") and dst.startswith("garmin"):
                    garmin_service.sync_garmin_to_garmin(db, current_user, act.id)
                
                # 佳明 -> 高驰
                elif src.startswith("garmin") and dst == "coros":
                    coros_service.sync_garmin_to_coros(db, current_user, act.id)
                
                # 高驰 -> 佳明
                elif src == "coros" and dst.startswith("garmin"):
                    target_region = "CN" if dst == "garmin_cn" else "GLOBAL"
                    garmin_service.sync_coros_to_garmin(db, current_user, act.id, target_region)
            except Exception:
                # 即使单个同步失败也继续处理后续记录
                continue

    return {"status": "success"}
