from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.db.session import get_db
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter()

@router.get("")
def get_user(
    username: Optional[str] = Query(None, description="通过用户名查询"),
    email: Optional[str] = Query(None, description="通过电子邮箱查询"),
    db: Session = Depends(get_db)
):   
    """
    获取用户信息。支持通过 username 或 email 进行查询。
    接口格式示例：
    - /user?username=inrenping
    - /user?email=test@example.com
    """
    query = db.query(User).filter(User.active == True)

    # 根据传入的参数动态构建查询条件
    if username:
        query = query.filter(User.user_name == username)
    elif email:
        query = query.filter(User.user_email == email)
    else:
        # 如果既没有传 username 也没有传 email，抛出 400 错误
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 username 或 email 其中之一作为查询参数"
        )

    user = query.first()
    
    if not user:
        identifier = username or email
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到用户: {identifier}"
        )
    return user

@router.get("/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    通过此接口验证 Token 有效性。
    如果 Token 无效，FastAPI 会自动返回 401。
    如果有效，将返回当前登录的用户信息。
    """
    return {
        "status": "token_valid",
        "user": {
            "id": current_user.user_id,
            "username": current_user.user_name,
            "email": current_user.user_email
        }
    }