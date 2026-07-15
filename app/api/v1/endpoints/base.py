import time
import json
from enum import Enum
from typing import Optional
from datetime import datetime, timedelta

from fastapi.responses import StreamingResponse
from sqlalchemy import desc, text, func
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.user import User

from app.services import (
    base_connect_service,
    base_activity_service,
    coros_service,
    garmin_service,
)
from app.utils.activity_type_config import ACTIVITY_CONFIG
from pydantic import BaseModel

router = APIRouter()


class ActionType(str, Enum):
    ADD = "add"
    UPDATE = "update"


class LoginRequest(BaseModel):
    """登录请求模型"""

    id: int
    region: str
    email: str
    password: str
    action: ActionType
    master: Optional[bool] = False


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "UP", "database": "OK"}
    except Exception as e:
        # 如果数据库连接断开或报错，直接抛出 500 异常
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database connection failed: {str(e)}",
        )


@router.get("/getConnectConfigs")
def get_connect_config(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    获取当前用户的连接配置。
    """
    connect_configs = base_connect_service.get_connects(db, current_user)
    return connect_configs


@router.get("/testConnect")
def test_connect(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试连接"""
    return base_connect_service.test_connect(id, db, current_user)


@router.post("/login")
def login(
    login_request: LoginRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    登录并将认证信息存入数据库。
    成功后将保存 accessToken 到对应的连接表中。
    """
    # 查找现有连接（通过用户ID、账号和区域）
    existing_connect = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.account == login_request.email,
            BaseConnect.region == login_request.region,
        )
        .first()
    )

    # 根据 action 执行不同的逻辑
    if login_request.action == ActionType.ADD:
        # add 场景：校验是否已存在，存在则拦截
        if existing_connect:
            return {"status": "error", "message": "该账号已存在，请不要重复添加。"}
    elif login_request.action == ActionType.UPDATE:
        # update 场景：校验是否已存在，不存在则拦截
        if not existing_connect:
            return {"status": "error", "message": "该账号不存在，无法执行更新操作。"}

    # 执行登录操作
    base_connect = base_connect_service.perform_login(
        id=login_request.id,
        email=login_request.email,
        password=login_request.password,
        region=login_request.region,
        db=db,
        current_user=current_user,
    )
    if not base_connect:
        return {"status": "error", "message": "登录失败"}
    else:
        # 先把其他账号的 master 置为 False
        db.query(BaseConnect).filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.id != base_connect.id,
        ).update({"master": False}, synchronize_session=False)
        # 更新是否主数据源字段
        base_connect.master = login_request.master
        db.commit()
        db.refresh(base_connect)
        return {"status": "success", "message": "登录成功", "data": base_connect.id}


@router.post("/relogin")
def relogin_connect(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    重新登录指定的第三方平台连接并更新认证信息。
    成功后将更新 accessToken 或其他凭证到对应的 BaseConnect 记录中。
    Args:
        connect_id (int, optional): 要重新登录的连接ID。如果为空，则无法重新登录。
        current_user (User): 当前认证用户。
        db (Session): 数据库会话。
    Returns:
        dict: 包含状态、消息和更新后的连接ID。
    """
    if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}
    base_connect = base_connect_service.perform_relogin(connect_id, db, current_user)
    if not base_connect:
        return {"status": "error", "message": "重新登录失败"}
    return {"status": "success", "message": "重新登录成功", "data": base_connect.id}


@router.get("/getActivitiesByPage")
def get_activities_by_page(
    connect_id: int,
    page_size: int = 10,
    page_count: int = 1,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sport_types: Optional[str] = None,
    name: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. 校验连接凭证
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.id == connect_id, BaseConnect.user_id == current_user.id)
        .first()
    )
    if not base_connect:
        return {"status": "success", "data": [], "total": 0}

    # 2. 构建基础查询
    query = db.query(BaseActivity).filter(
        BaseActivity.user_id == current_user.id,
        BaseActivity.base_connect_id == base_connect.id,
    )

    # 3. 组合时间过滤条件
    if start_date:
        query = query.filter(BaseActivity.start_time_local >= start_date)
    if end_date:
        query = query.filter(BaseActivity.start_time_local <= end_date)

    # 4. 增加运动类型过滤 (支持多选，逗号分隔)
    if sport_types:
        key_list = [t.strip() for t in sport_types.split(",")]
        # 从配置中查找对应的 key (整数)
        key_list.extend(
            [item["name"] for item in ACTIVITY_CONFIG if item["key"] in key_list]
        )
        # 将 key 和 name 都放入查询条件 (满足任意一个即可)
        query = query.filter(BaseActivity.sport_type_raw.in_(key_list))

    # 5. 增加名称模糊搜索 (同时匹配 name 和 activity_name)
    if name:
        search_pattern = f"%{name}%"
        query = query.filter(BaseActivity.activity_name.ilike(search_pattern))

    # 6. 计算符合条件的总条数
    total = query.count()

    # 7. 执行分页与排序查询
    result = (
        query.order_by(desc(BaseActivity.start_time_local))
        .limit(page_size)
        .offset((page_count - 1) * page_size)
        .all()
    )

    return {"status": "success", "data": result, "total": total}


@router.get("/getActivity")
def get_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    activity = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.id,
            BaseActivity.id == id,
        )
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="未找到对应的活动记录")
    return {"status": "success", "data": activity}


@router.post("/pullFullActivities")
def pull_full_activities(
    connect_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    全量获取运动记录并保存到本地数据库。
    采用分页拉取逻辑，通过 labelId 进行去重判断。
    """
    return base_activity_service.pull_full_activities(
        connect_id=connect_id, incremental=False, db=db, current_user=current_user
    )


@router.post("/pullNewActivities")
def pull_new_activities(
    connect_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    全量获取高驰运动记录并保存到本地数据库。
    采用分页拉取逻辑，通过 labelId 进行去重判断。
    """
    return base_activity_service.pull_full_activities(
        connect_id=connect_id, incremental=True, db=db, current_user=current_user
    )


@router.get("/downloadActivity/{id}")
def download_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    通过 activity_id 定位具体的运动记录，并返回文件内容。
    """
    return base_activity_service.download_activity(id, db, current_user)


@router.post("/uploadActivity2Target/{activity_id}/{target_connect_id}")
def upload_activity_to_target(
    activity_id: int,
    target_connect_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从活动所属区域下载 FIT，上传到另一个账号
    """
    return base_activity_service.upload_activity_to_target(
        activity_id=activity_id,
        target_connect_id=target_connect_id,
        db=db,
        current_user=current_user,
    )


class TaskRequest(BaseModel):
    source_id: int
    target_id: int
    count: int


@router.post("/execute")
def execute_task(
    request: TaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试 SSE"""
    print(
        f"🔔 收到数据同步请求 -> user_id: {current_user.id}, source_id: {request.source_id}, target_id: {request.target_id}"
    )

    return StreamingResponse(
        log_stream_generator(
            request.source_id, request.target_id, request.count, current_user, db
        ),
        media_type="text/event-stream",
    )


def log_stream_generator(
    source_id: int,
    target_id: int,
    count: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    负责在长连接中，源源不断地 yield 推送数据给客户端。
    注意：这里必须遵循 SSE 格式 => data: <你的字符串数据>\n\n
    """
    if source_id == target_id:
        yield f"data: {json.dumps({"level": "info", "message": f"❌ 两个账号相同不需要同步"}, ensure_ascii=False)}\n\n"

        return
    try:

        yield f"data: {json.dumps({"level": "info", "message": f"🔔 [0/7][[0/7][Task-{current_user.id}-{source_id}-{target_id}] 正在构建同步任务Task-{current_user.id}-{source_id}-{target_id}] 正在构建同步任务"}, ensure_ascii=False)}\n\n"

        time.sleep(0.5)

        source_config = (
            db.query(BaseConnect)
            .filter(BaseConnect.id == source_id, BaseConnect.user_id == current_user.id)
            .first()
        )
        if not source_config:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [1/7] 未找到源平台 {source_id} 的连接配置"}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({"level": "info", "message": f"🤖 [1/7]源平台{ source_id } 鉴权"}, ensure_ascii=False)}\n\n"
        source_config = base_connect_service.perform_relogin(
            source_id, db, current_user
        )
        if not source_config:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [1/7]源平台{ source_id } 鉴权失败"}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({"level": "success", "message": f"🤖 [1/7]源平台{ source_id } 鉴权通过"}, ensure_ascii=False)}\n\n"
        time.sleep(1)
        yield f"data: {json.dumps({"level": "info", "message": f"🏗️ [2/7]源平台{ source_id } 开始增量同步数据"}, ensure_ascii=False)}\n\n"
        source_sync_result = base_activity_service.pull_full_activities(
            connect_id=source_id, incremental=True, db=db, current_user=current_user
        )
        time.sleep(10)
        if source_sync_result.get("status") == "success":
            yield f"data: {json.dumps({"level": "success", "message": f"🏗️ [2/7]源平台{ source_id } 增量同步数据成功"}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [2/7]源平台{ source_id } 增量同步数据失败"}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({"level": "info", "message": f"📦 [3/7]源平台{ source_id } 开始获取最新 {count} 条数据"}, ensure_ascii=False)}\n\n"
        source_activities = (
            db.query(BaseActivity)
            .filter(
                BaseActivity.base_connect_id == source_id,
                BaseActivity.user_id == current_user.id,
            )
            .order_by(desc(BaseActivity.start_time_local))
            .limit(count)
            .all()
        )
        if not source_activities:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [3/7]源平台{ source_id } 获取最新 {count} 条数据失败"}, ensure_ascii=False)}\n\n"
            return
        else:
            yield f"data: {json.dumps({"level": "success", "message": f"📦 [3/7]源平台{ source_id } 获取最新 {count} 条数据成功"}, ensure_ascii=False)}\n\n"

        target_config = base_connect_service.perform_relogin(
            target_id, db, current_user
        )
        if not target_config:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [4/7]目标平台{ target_id } 鉴权失败"}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({"level": "success", "message": f"🤖 [4/7]目标平台{ target_id } 鉴权通过"}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({"level": "info", "message": f"🏗️ [5/7]目标平台{ target_id } 开始增量同步数据"}, ensure_ascii=False)}\n\n"
        target_sync_result = base_activity_service.pull_full_activities(
            connect_id=target_id, incremental=True, db=db, current_user=current_user
        )
        if target_sync_result.get("status") == "success":
            yield f"data: {json.dumps({"level": "success", "message": f"🏗️ [5/7]目标平台{ target_id } 增量同步数据成功"}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [5/7]目标平台{ target_id } 增量同步数据失败"}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({"level": "info", "message": f"📦 [6/7]目标平台{ target_id } 开始获取最新 {count} 条数据"}, ensure_ascii=False)}\n\n"
        target_activities = (
            db.query(BaseActivity)
            .filter(
                BaseActivity.base_connect_id == target_id,
                BaseActivity.user_id == current_user.id,
            )
            .order_by(desc(BaseActivity.start_time_local))
            .limit(count)
            .all()
        )
        if not target_activities:
            yield f"data: {json.dumps({"level": "error", "message": f"❌ [6/7]目标平台{ target_id } 获取最新 {count} 条数据失败"}, ensure_ascii=False)}\n\n"
            return
        else:
            yield f"data: {json.dumps({"level": "success", "message": f"📦 [6/7]目标平台{ target_id } 获取最新 {count} 条数据成功"}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({"level": "info", "message": f"✨ [5/7]开始比较两个平台最新的 {count} 条数据"}, ensure_ascii=False)}\n\n"

        intersection = []  # 两个平台都有且运动特征一致的记录（交集）
        diff_source_only = []  # 源平台独有，需要同步到目标平台的数据

        # 用一个集合来记录目标平台中哪些数据已经被成功匹配了
        matched_target_ids = set()

        # 外层循环：遍历源平台
        for item_a in source_activities:
            is_matched = False

            # 内层循环：遍历目标平台
            for item_b in target_activities:
                # 如果目标平台的这条记录已经被别的运动匹配过了，直接跳过
                if item_b.activity_id in matched_target_ids:
                    continue

                if base_activity_service.is_same_activity(item_a, item_b):
                    print(
                        f"✨ 属性比对成功：源平台[{item_a.activity_id}] 与 目标平台[{item_b.activity_id}] 为同一运动"
                    )
                    intersection.append({"source": item_a, "target": item_b})

                    # 标记这条目标平台数据已被消耗
                    matched_target_ids.add(item_b.activity_id)
                    is_matched = True
                    break

            # 如果内层循环全部跑完，源平台这条数据依然没有找到匹配的目标，说明是源平台独有的
            if not is_matched:
                print(
                    f"📌 源平台[{item_a.activity_id}] 在目标平台没有找到匹配的运动，归为源平台 {source_id} 独有"
                )
                diff_source_only.append(item_a)

        yield f"data: {json.dumps({"level": "info", "message": f"✨ [5/7]筛选之后得到 源平台 {source_id} 有 {len(diff_source_only)} 条需要上传的数据"}, ensure_ascii=False)}\n\n"

        if len(diff_source_only) == 0:
            yield f"data: {json.dumps({"level": "info", "message": f"✨ [5/7]源平台没有需要上传的数据，完成同步"}, ensure_ascii=False)}\n\n"
            return
        if len(diff_source_only) > 0:
            yield f"data: {json.dumps({"level": "info", "message": f"📦 [6/7]开始从源平台 {source_id} 下载 {len(diff_source_only)} 条记录"}, ensure_ascii=False)}\n\n"

        source_file_list = []
        for source_item in diff_source_only:
            if source_item.source_type == "coros":
                file_data, filename = coros_service.download_coros_activity_response(
                    activity_id=source_item.id,
                    connect_id=source_id,
                    db=db,
                    current_user=current_user,
                )
                source_file_list.append((file_data.content, filename))
            else:
                file_data = coros_service._download_garmin_activity(
                    activity=source_item,
                    garmin_config=source_config,
                    current_user=current_user,
                )
                source_file_list.append((file_data, str(source_item.activity_id)))

        yield f"data: {json.dumps({"level": "success", "message": f"📦 [6/7]从源平台 {source_id} 下载 {len(diff_source_only)} 条记录完成"}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({"level": "info", "message": f"🚀 [7/7]向目标平台 {target_id} 上传 {len(diff_source_only)} 条记录"}, ensure_ascii=False)}\n\n"
        for source_file, filename in source_file_list:
            if target_config.source_type == "coros":
                upload_result = coros_service._upload_fit_zip_to_coros(
                    db, current_user, target_config, source_file, filename
                )
            elif target_config.source_type == "garmin":
                upload_result = garmin_service._upload_file_to_garmin(
                    current_user=current_user,
                    db=db,
                    target_config=target_config,
                    file_data=source_file,
                    filename=filename,
                )
            else:
                upload_result = {"status": "error", "message": f"不支持的目标平台类型: {target_config.source_type}"}
            yield f"data: {json.dumps({"level": "info", "message": f"上传文件 {filename} 结果: {json.dumps(upload_result, ensure_ascii=False)}"}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({"level": "success", "message": f"🚀 [7/7]向目标平台 {target_id} 上传 {len(diff_source_only)} 条记录成功"}, ensure_ascii=False)}\n\n"
        # 推送所有任务结束的暗号

        yield "data: [DONE]\n\n"

    except GeneratorExit:
        print(
            f"🛑 检测到客户端中断了连接，任务 [Task-{current_user.id}-{source_id}-{target_id}] 的流式推送已停止。"
        )
        raise


@router.get("/getRunningTotal")
def get_running_total(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    获取当前用户的月跑量和年跑量
    只统计主账号（BaseConnect.master = True）的跑步数据
    跑步类型包括：running, treadmill_running, trail_running, track_running, indoor_running
    """
    # 1. 获取当前用户的主账号连接
    master_connect = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.master == True,
        )
        .first()
    )

    if not master_connect:
        return {
            "status": "success",
            "data": {
                "monthly_total": 0,
                "yearly_total": 0,
            },
        }

    # 2. 定义跑步类型列表
    running_types = [
        "running",
        "treadmill_running",
        "trail_running",
        "track_running",
        "indoor_running",
        "100",
        "101",
        "102",
        "103",
    ]

    # 3. 获取当前时间
    now = datetime.now()
    current_year = now.year
    current_month = now.month

    # 4. 查询年跑量（当前年所有跑步数据）
    yearly_activities = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.id,
            BaseActivity.base_connect_id == master_connect.id,
            BaseActivity.sport_type_raw.in_(running_types),
            func.extract("year", BaseActivity.start_time_local) == current_year,
        )
        .all()
    )

    yearly_total = (
        sum(
            float(activity.distance_meters)
            for activity in yearly_activities
            if activity.distance_meters is not None
        )
        or 0
    )
    yearly_count = len(yearly_activities)  # 年跑步次数
    yearly_duration = (
        sum(
            float(activity.duration_seconds)
            for activity in yearly_activities
            if activity.duration_seconds is not None
        )
        / 3600
        if yearly_activities
        else 0
    )  # 年累计时间（小时）

    # 5. 查询月跑量（当前月所有跑步数据）
    monthly_activities = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.id,
            BaseActivity.base_connect_id == master_connect.id,
            BaseActivity.sport_type_raw.in_(running_types),
            func.extract("year", BaseActivity.start_time_local) == current_year,
            func.extract("month", BaseActivity.start_time_local) == current_month,
        )
        .all()
    )

    monthly_total = (
        sum(
            float(activity.distance_meters)
            for activity in monthly_activities
            if activity.distance_meters is not None
        )
        or 0
    )
    monthly_count = len(monthly_activities)  # 月跑步次数
    monthly_duration = (
        sum(
            float(activity.duration_seconds)
            for activity in monthly_activities
            if activity.duration_seconds is not None
        )
        / 3600
        if monthly_activities
        else 0
    )  # 月累计时间（小时）

    # 6. 计算目标和完成度（公里为单位）
    yearly_target = 2026  # 年目标 2026 公里
    monthly_target = yearly_target / 12  # 月目标

    # 转换为公里
    monthly_total_km = monthly_total / 1000 if monthly_total else 0
    yearly_total_km = yearly_total / 1000 if yearly_total else 0

    return {
        "status": "success",
        "data": {
            "monthly_total": round(
                monthly_total_km or 0, 2
            ),  # 月跑量（公里，保留2位小数）
            "monthly_target": round(monthly_target, 2),  # 月目标（公里，保留2位小数）
            "monthly_count": monthly_count,  # 月跑步次数
            "monthly_duration": round(
                monthly_duration or 0, 2
            ),  # 月累计时间（小时，保留2位小数）
            "yearly_total": round(
                yearly_total_km or 0, 2
            ),  # 年跑量（公里，保留2位小数）
            "yearly_target": yearly_target,  # 年目标（2026公里）
            "yearly_count": yearly_count,  # 年跑步次数
            "yearly_duration": round(
                yearly_duration or 0, 2
            ),  # 年累计时间（小时，保留2位小数）
        },
    }
