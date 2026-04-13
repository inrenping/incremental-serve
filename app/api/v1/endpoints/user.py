from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.db.session import get_db
from app.models.user import User

# 注意：建议将 prefix 设置为 "/user"
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