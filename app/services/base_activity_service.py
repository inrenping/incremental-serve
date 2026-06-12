import io
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import base_connect_service, garmin_service, coros_service


def pull_full_activities(
     current_user: User, db: Session, connect_id: int, incremental: bool = False
) -> dict:
    """全量/增量拉取数据。"""
    base_connect = db.query(BaseConnect).filter(BaseConnect.id == connect_id).first()
    if not base_connect:
        return {"status": "error", "message": "未找到授权配置，请先登录获取授权。"}
    # 确认 Token 有效性
    base_connect = base_connect_service.perform_relogin(connect_id, db, current_user)
    platform = base_connect.source_type
    if platform == "garmin":
        # 调用 Garmin 国际区同步接口
        return garmin_service.pull_full_garmin_activities(
            current_user=current_user,
            db=db,
            connect_id=base_connect.id,
            incremental=incremental,
        )
    elif platform == "garmin_cn":
        # 调用 Garmin 中国区同步接口
        return garmin_service.pull_full_garmin_activities(
            current_user=current_user,
            db=db,
            connect_id=base_connect.id,
            incremental=incremental,
        )
    elif platform == "coros":
        # 调用 Coros 同步接口
        return coros_service.pull_full_coros_activities(
            current_user=current_user,
            db=db,
            connect_id=base_connect.id,
            incremental=incremental,
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的平台类型")


def download_activity(activity_id: int, db: Session, current_user: User):
    """下载文件"""
    if not activity_id:
        return {"status": "error", "message": "缺少 activity_id 参数，无法下载。"}
    base_activity = db.query(BaseActivity).filter(BaseActivity.id == activity_id).first()
    base_connect = (
        db.query(BaseConnect).filter(BaseConnect.id == base_activity.base_connect_id).first()
    )
    # Token 有效性
    base_connect = base_connect_service.perform_relogin(
        base_connect.id, db, current_user
    )
    if not base_activity:
        return {"status": "error", "message": "未找到对应的活动记录"}
    # 高驰下载
    if base_activity and base_activity.source_type == "coros":
        file_response, filename = coros_service.download_coros_activity_response(
            db, current_user,base_connect.id, activity_id
        )
        return StreamingResponse(
            file_response.iter_content(chunk_size=8192),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    # 佳明下载
    elif base_activity and base_activity.source_type == "garmin":
        # 1. 获取 Response 对象（此时连接仍处于 open 状态）
        file_data, filename = garmin_service.get_garmin_activity_download_info(
            db, current_user, activity_id
        )

        # 直接将 bytes 转换为字节流
        stream = io.BytesIO(file_data)

        return StreamingResponse(
            stream,
            media_type="application/octet-stream",  # 建议统一使用 octet-stream，兼容性更好
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return {"status": "error", "message": "不支持的设备类型"}

def upload_activity_to_target(
    activity_id: int, target_connect_id: int, db: Session, current_user: User
):
    """把运动数据同步到指定账号"""
    source_activity = db.query(BaseActivity).filter(BaseActivity.id == activity_id).first()
    if not source_activity:
        return {"status": "error", "message": "未找到对应的活动记录"}
    source_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.id == source_activity.base_connect_id)
        .first()
    )
    target_connect = (
        db.query(BaseConnect).filter(BaseConnect.id == target_connect_id).first()
    )   
    try:
        if not target_connect:
            return {"status": "error", "message": "未找到对应的目标账号"}
        # TODO 推送之前先去查一下目标记录是否已经存在（根据时间和距离判断）
        if source_connect.id == target_connect.id:
            return {"status": "error", "message": "源账号和目标账号不能相同"}
        # 开始分情况推送
        if (
            source_connect.source_type == "garmin"
            and target_connect.source_type == "coros"
        ):
            return coros_service.sync_garmin_to_coros(db, current_user, activity_id, target_connect.id)
        elif (
            source_connect.source_type == "coros"
            and target_connect.source_type == "coros"
        ):
            return {
            "status": "error",
            "message": "高驰导入失败: 同一个平台的不需要同步",
            }
        elif (
            source_connect.source_type == "garmin"
            and target_connect.source_type == "garmin"
        ):
            return garmin_service.sync_garmin_to_garmin(db, current_user, activity_id,target_connect.id)
        elif (
            source_connect.source_type == "coros"
            and target_connect.source_type == "garmin"
        ):
            return garmin_service.sync_coros_to_garmin(db, current_user, activity_id,target_connect.id)
    except Exception as e:
        print(f"上传失败: {str(e)}")
        return {"status": "error", "message": str(e)}


def is_same_activity(
        source_activity: BaseActivity,
        target_activity: BaseActivity,
) -> bool:
    """判断是不是同一个运动记录（必须同时满足时间和距离条件）。"""

    # 1. 基础前置校验：如果两边缺少时间，无法比对，直接判定为不是同一条
    if not source_activity.start_time_gmt or not target_activity.start_time_gmt:
        return False

    # 2. 校验条件一：时间差异必须在 5 分钟 (300秒) 以内
    time_diff = abs((source_activity.start_time_gmt - target_activity.start_time_gmt).total_seconds())
    if time_diff > 300:
        return False

    # 3. 校验条件二：距离差异必须在 5% 以内
    s_dist = float(source_activity.distance_meters or 0)
    t_dist = float(target_activity.distance_meters or 0)

    # 如果两个平台记录的距离都为 0（比如某些无距离数据的静态冥想或纯时间健身），则认为距离条件默认通过
    if s_dist > 0 or t_dist > 0:
        max_dist = max(s_dist, t_dist)
        dist_diff_ratio = abs(s_dist - t_dist) / max_dist
        if dist_diff_ratio > 0.05:
            return False  # 距离不及格，直接淘汰

    # 4. （时间OK 且 距离OK），才能返回 True
    return True