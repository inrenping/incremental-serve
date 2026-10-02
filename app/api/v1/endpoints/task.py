from typing import List, Optional
from pydantic import BaseModel, Field
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.security import get_current_user
from app.db.session import get_db
from app.services import base_connect_service
from app.models.task import Task
from app.models.task_item import TaskItem
from app.models.task_result import TaskResult
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

# 单个任务执行次数上限：同步对数 × 触发小时数
MAX_TASK_EXECUTIONS_PER_DAY = 8
MAX_TASK_ITEMS = 8
MAX_TASK_HOURS = 8
# 每个用户只允许一个任务：多同步方向 / 多时间点都在任务内配置
MAX_TASKS_PER_USER = 1


class TaskItemPayload(BaseModel):
    """任务子项：一条「源 -> 目标」同步对"""

    connect_source_id: int
    connect_target_id: int


class SaveTaskRequest(BaseModel):
    """新增/修改任务请求模型。传入 id 为修改，不传 id 为新增"""

    id: Optional[int] = None
    hours: List[int] = Field(default_factory=list)
    items: List[TaskItemPayload] = Field(default_factory=list)
    is_active: Optional[bool] = True


def _validate_hours(hours: List[int]) -> Optional[str]:
    """校验触发小时列表，返回错误信息或 None"""
    if not hours:
        return "请至少选择一个执行时间"
    if len(set(hours)) != len(hours):
        return "执行时间不能重复"
    if len(hours) > MAX_TASK_HOURS:
        return f"执行时间最多 {MAX_TASK_HOURS} 个"
    if any(not isinstance(h, int) or h < 0 or h > 23 for h in hours):
        return "执行时间必须在 0-23 之间"
    return None


def _validate_items(
    db: Session,
    current_user: User,
    items: List[TaskItemPayload],
) -> Optional[str]:
    """校验同步对列表，返回错误信息或 None"""
    if not items:
        return "请至少添加一条同步配置（源 -> 目标）"
    if len(items) > MAX_TASK_ITEMS:
        return f"同步配置最多 {MAX_TASK_ITEMS} 条"
    seen = set()
    for item in items:
        if item.connect_source_id == item.connect_target_id:
            return "源账号与目标账号不能相同"
        key = (item.connect_source_id, item.connect_target_id)
        if key in seen:
            return "同步配置不能重复"
        seen.add(key)
        _, source_error, _ = base_connect_service.resolve_owned_connect(
            db, current_user, item.connect_source_id
        )
        _, target_error, _ = base_connect_service.resolve_owned_connect(
            db, current_user, item.connect_target_id
        )
        errors = []
        if source_error:
            errors.append(f"源账号：{source_error}")
        if target_error:
            errors.append(f"目标账号：{target_error}")
        if errors:
            return "；".join(errors)
    return None


def _task_to_dict(task: Task, items: List[TaskItem]) -> dict:
    """序列化任务（含 hours 与 items），供前端使用"""
    return {
        "id": task.id,
        "user_id": task.user_id,
        "hours": task.hours or ([task.hour] if task.hour is not None else []),
        "is_active": task.is_active,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "items": [
            {
                "id": item.id,
                "connect_source_id": item.connect_source_id,
                "connect_target_id": item.connect_target_id,
            }
            for item in items
        ],
    }


@router.get("")
def get_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的所有任务（含触发小时与同步配置）"""
    tasks = (
        db.query(Task)
        .filter(Task.user_id == current_user.id)
        .order_by(desc(Task.created_at))
        .all()
    )
    task_ids = [t.id for t in tasks]
    items = (
        db.query(TaskItem)
        .filter(TaskItem.task_id.in_(task_ids))
        .order_by(TaskItem.id)
        .all()
        if task_ids
        else []
    )
    items_by_task = {}
    for item in items:
        items_by_task.setdefault(item.task_id, []).append(item)
    return {
        "status": "success",
        "data": [
            _task_to_dict(task, items_by_task.get(task.id, []))
            for task in tasks
        ],
    }


@router.post("")
def save_task(
    request: SaveTaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新增或修改任务。传入 id 修改，不传 id 新增"""
    hours = sorted(set(request.hours))
    logger.info(
        "[task] save request id=%s hours=%s items=%s",
        request.id,
        hours,
        [(i.connect_source_id, i.connect_target_id) for i in request.items],
    )
    error = _validate_hours(hours)
    if error:
        return {"status": "error", "message": error}

    error = _validate_items(db, current_user, request.items)
    if error:
        return {"status": "error", "message": error}

    if len(hours) * len(request.items) > MAX_TASK_EXECUTIONS_PER_DAY:
        return {
            "status": "error",
            "message": f"同步配置数 × 执行时间数不能超过 {MAX_TASK_EXECUTIONS_PER_DAY} 次/天",
        }

    if request.id:
        task = (
            db.query(Task)
            .filter(Task.id == request.id, Task.user_id == current_user.id)
            .first()
        )
        if not task:
            return {"status": "error", "message": "任务不存在或无权访问"}
        task.hours = hours
        task.is_active = request.is_active
        # 旧字段同步为首条同步对，保证仍读旧字段的逻辑（如旧调度器）不会拿到空值
        task.hour = hours[0]
        task.connect_source_id = request.items[0].connect_source_id
        task.connect_target_id = request.items[0].connect_target_id
        # 同步配置整体替换
        db.query(TaskItem).filter(TaskItem.task_id == task.id).delete()
        db.flush()
    else:
        count = db.query(Task).filter(Task.user_id == current_user.id).count()
        if count >= MAX_TASKS_PER_USER:
            return {
                "status": "error",
                "message": (
                    f"每个用户只能创建 {MAX_TASKS_PER_USER} 个任务，"
                    "多个同步方向和执行时间请在已有任务中编辑添加"
                ),
            }
        # 旧字段（hour / connect_source_id / connect_target_id）仍写入首条同步对：
        # 生产库上这几个字段仍是 NOT NULL，不写会导致新建任务直接报 500
        task = Task(
            user_id=current_user.id,
            hours=hours,
            is_active=request.is_active,
            hour=hours[0],
            connect_source_id=request.items[0].connect_source_id,
            connect_target_id=request.items[0].connect_target_id,
        )
        db.add(task)
        db.flush()

    for item in request.items:
        db.add(
            TaskItem(
                task_id=task.id,
                connect_source_id=item.connect_source_id,
                connect_target_id=item.connect_target_id,
            )
        )

    db.commit()
    db.refresh(task)
    items = (
        db.query(TaskItem)
        .filter(TaskItem.task_id == task.id)
        .order_by(TaskItem.id)
        .all()
    )
    logger.info(
        "[task] saved task %s with %d items (requested %d)",
        task.id,
        len(items),
        len(request.items),
    )
    return {"status": "success", "data": _task_to_dict(task, items)}


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除任务，连带删除其同步配置（t_task_item）与执行记录（t_task_result）"""
    from sqlalchemy import text

    task = (
        db.query(Task)
        .filter(Task.id == task_id, Task.user_id == current_user.id)
        .first()
    )
    if not task:
        return {"status": "error", "message": "任务不存在或无权访问"}

    # 历史遗留的明细表无 ORM 模型，直接用 SQL 清理，缺失该表时忽略
    try:
        db.execute(
            text(
                "DELETE FROM t_task_result_detail "
                "WHERE task_result_id IN (SELECT id FROM t_task_result WHERE task_id = :task_id)"
            ),
            {"task_id": task_id},
        )
    except Exception as e:
        db.rollback()
        logger.warning(f"clean t_task_result_detail failed: {e}")

    db.query(TaskResult).filter(TaskResult.task_id == task_id).delete()
    db.query(TaskItem).filter(TaskItem.task_id == task_id).delete()
    db.delete(task)
    db.commit()
    return {"status": "success", "message": "任务已删除"}


@router.post("/cron-execute")
def cron_execute(
    db: Session = Depends(get_db),
):
    """
    定时任务回调接口（无认证，仅内部/定时任务调用）。
    遍历所有有效 task，若当前小时匹配 task.hours 之一，
    则执行该任务下所有「源 -> 目标」同步对，并将 SSE 输出记录到 task_result 中。
    """
    import json
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app.api.v1.endpoints.base import log_stream_generator

    now_utc = datetime.now(timezone.utc)
    tasks = db.query(Task).filter(Task.is_active == True).all()

    executed_count = 0
    for task in tasks:
        user = db.query(User).filter(User.id == task.user_id).first()
        if not user:
            continue

        task_hours = task.hours or ([task.hour] if task.hour is not None else [])
        if not task_hours:
            continue

        # 根据用户的时区计算当前本地小时
        user_tz = user.timezone or "Asia/Shanghai"
        local_hour = now_utc.astimezone(ZoneInfo(user_tz)).hour
        if local_hour not in task_hours:
            continue

        items = db.query(TaskItem).filter(TaskItem.task_id == task.id).all()
        messages = []
        for item in items:
            try:
                for sse_data in log_stream_generator(
                    source_id=item.connect_source_id,
                    target_id=item.connect_target_id,
                    count=10,
                    current_user=user,
                    db=db,
                ):
                    messages.append(sse_data)
            except Exception as e:
                messages.append(
                    f"data: {json.dumps({'level': 'error', 'message': f'执行异常: {str(e)}'}, ensure_ascii=False)}\n\n"
                )

        if messages:
            task_result = TaskResult(
                task_id=task.id,
                task_messages="\n".join(messages),
            )
            db.add(task_result)
            executed_count += 1

    db.commit()
    return {
        "status": "success",
        "message": f"已检查 {len(tasks)} 个任务，匹配当前小时并执行了 {executed_count} 个",
    }


@router.post("/cron-execute2")
def cron_execute2():
    """
    手动触发 cron-execute2 定时任务（复用 scheduler 内部逻辑）。
    遍历所有有效 task，若当前小时匹配 task.hours 之一，
    则对该任务下每个「源 -> 目标」同步对调用 execute_task2，
    并将结果 JSON 记录到 task_result 日志中。
    """
    from app.core.scheduler import run_cron_execute2_internal

    run_cron_execute2_internal()
    return {"status": "success", "message": "cron-execute2 triggered successfully"}


@router.get("/{task_id}")
def get_task_results(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定任务的所有执行结果"""
    task = (
        db.query(Task)
        .filter(Task.id == task_id, Task.user_id == current_user.id)
        .first()
    )
    if not task:
        return {"status": "error", "message": "任务不存在或无权访问"}

    results = (
        db.query(TaskResult)
        .filter(TaskResult.task_id == task_id)
        .order_by(desc(TaskResult.created_at))
        .all()
    )
    return {"status": "success", "data": results}
