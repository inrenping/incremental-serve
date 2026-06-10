from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.log_operation import OperationLog
from app.models.log_api import SysLog
from app.models.user import User

router = APIRouter()


@router.get("")
def get_operation_logs(
    page_size: int = 5,
    page_count: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取最新的 5 条操作日志"""
    logs = (
        db.query(OperationLog)
        .filter(OperationLog.user_id == current_user.id)
        .order_by(desc(OperationLog.created_at))
        .limit(page_size)
        .offset((page_count - 1) * page_size)
        .all()
    )
    total = db.query(OperationLog).filter(OperationLog.user_id == current_user.id).count()
    return {"status": "success", "data": logs, "total": total}


@router.get("/syslog")
def get_sys_logs(
    page_size: int = 10,
    page_count: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取 API 调用日志"""
    sysLogs = (
        db.query(SysLog)
        .filter(SysLog.user_id == current_user.id)
        .order_by(desc(SysLog.created_at))
        .limit(page_size)
        .offset((page_count - 1) * page_size)
        .all()
    )
    total = db.query(SysLog).filter(SysLog.user_id == current_user.id).count()
    return {"status": "success", "data": sysLogs, "total": total}
