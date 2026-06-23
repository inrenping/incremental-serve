from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.task import Task
from app.models.task_result import TaskResult
from app.models.user import User

router = APIRouter()


class SaveTaskRequest(BaseModel):
    """新增/修改任务请求模型。传入 id 为修改，不传 id 为新增"""

    id: Optional[int] = None
    connect_source_id: int
    connect_target_id: int
    hour: int
    is_active: Optional[bool] = True


@router.get("")
def get_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的所有任务"""
    tasks = (
        db.query(Task)
        .filter(Task.user_id == current_user.id)
        .order_by(desc(Task.created_at))
        .all()
    )
    return {"status": "success", "data": tasks}


@router.post("")
def save_task(
    request: SaveTaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新增或修改任务。传入 id 修改，不传 id 新增"""
    if request.id:
        task = (
            db.query(Task)
            .filter(Task.id == request.id, Task.user_id == current_user.id)
            .first()
        )
        if not task:
            return {"status": "error", "message": "任务不存在或无权访问"}
        task.connect_source_id = request.connect_source_id
        task.connect_target_id = request.connect_target_id
        task.hour = request.hour
        task.is_active = request.is_active
    else:
        count = db.query(Task).filter(Task.user_id == current_user.id).count()
        if count >= 3:
            return {"status": "error", "message": "每个用户最多只能创建 3 个任务"}
        task = Task(
            user_id=current_user.id,
            connect_source_id=request.connect_source_id,
            connect_target_id=request.connect_target_id,
            hour=request.hour,
            is_active=request.is_active,
        )
        db.add(task)
    db.commit()
    db.refresh(task)
    return {"status": "success", "data": task}


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
