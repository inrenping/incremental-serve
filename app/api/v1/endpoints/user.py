from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.models.user import User
from app.core.security import get_current_user
from app.services.user_service import get_user_info, get_user_social_info

router = APIRouter()

@router.get("")
def get_user(
    username: Optional[str] = None,
    email: Optional[str] = None,
    db: Session = Depends(get_db)
):   
    """
    获取用户信息。支持通过 username 或 email 进行查询。
    接口格式示例：
    - /user?username=inrenping
    - /user?email=test@example.com
    """
    return get_user_info(db, username=username, email=email)

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


@router.get("/socials")
def get_user_socials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """根据当前登录用户获取其社交登录信息。"""
    return get_user_social_info(db, current_user)

@router.delete("")
def delete_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
  return {"status": "success"}