from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.user import User

router = APIRouter()

@router.get("/username/{username}")
def get_user_by_name(username: str, db: Session = Depends(get_db)):   
    """
    通过用户名获取激活状态的用户信息。

    Args:
        username (str): 要查询的用户名。
        db (Session): 数据库会话实例，由 FastAPI 依赖注入提供。

    Returns:
        User: 返回查询到的用户对象模型。

    Raises:
        HTTPException: 如果用户不存在或未激活，抛出 404 错误。
    """
    user = db.query(User).filter(User.user_name == username, User.active == True).first()       
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with username '{username}' not found"
        )
    return user

@router.get("/useremail/{email}")
def get_user_by_email(email: str, db: Session = Depends(get_db)):
    """
    通过电子邮件获取激活状态的用户信息。

    Args:
        email (str): 用户的电子邮箱地址。
        db (Session): 数据库会话。

    Returns:
        User: 匹配的用户对象。

    Raises:
        HTTPException: 当找不到匹配且激活的用户时抛出。
    """
    user = db.query(User).filter(User.user_email == email, User.active == True).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"User with email '{email}' not found"
        )
    
    return user