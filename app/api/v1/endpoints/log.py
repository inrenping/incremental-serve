from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.operation_log import OperationLog
from app.models.user import User


router = APIRouter()


@router.get("")
def get_logs(    
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取最新的20条操作日志"""
    logs = db.query(OperationLog).filter(OperationLog.user_id == current_user.user_id).order_by(desc(OperationLog.created_at)).limit(20).all()
    return {"status": "success", "data": logs}