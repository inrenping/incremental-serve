"""用户管理服务"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi import HTTPException
from app.models.user import User


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """通过用户名获取活跃用户"""
    return db.query(User).filter(
        User.user_name == username,
        User.active == True
    ).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """通过邮箱获取用户"""
    return db.query(User).filter(User.user_email == email).first()


def get_active_user_by_email(db: Session, email: str) -> Optional[User]:
    """通过邮箱获取活跃用户"""
    return db.query(User).filter(
        User.user_email == email,
        User.active == True
    ).first()


def user_exists(db: Session, username: str = None, email: str = None) -> Optional[User]:
    """检查用户是否存在"""
    if username:
        return db.query(User).filter(User.user_name == username).first()
    if email:
        return db.query(User).filter(User.user_email == email).first()
    return None


def create_user(db: Session, username: str, email: str) -> User:
    """创建新用户"""
    now = datetime.now(timezone.utc)
    user = User(
        user_name=username,
        user_email=email,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    return user


def generate_unique_username(db: Session, base_name: str, email: str) -> str:
    """生成唯一的用户名"""
    candidate = base_name.strip() or email.split("@")[0]
    candidate = candidate[:30]
    existing = db.query(User).filter(User.user_name == candidate).first()
    if not existing:
        return candidate

    for suffix in range(1, 1000):
        username = f"{candidate[:28]}{suffix}"
        if not db.query(User).filter(User.user_name == username).first():
            return username

    return f"user_{int(datetime.now(timezone.utc).timestamp())}"


def get_user_info(db: Session, username: str = None, email: str = None) -> Dict[str, Any]:
    """
    获取用户信息（查询接口使用）
    支持通过用户名或邮箱查询，只返回活跃用户
    """
    if not username and not email:
        raise HTTPException(
            status_code=400,
            detail="必须提供 username 或 email 其中之一作为查询参数"
        )

    query = db.query(User).filter(User.active == True)

    if username:
        query = query.filter(User.user_name == username)
        identifier = username
    else:
        query = query.filter(User.user_email == email)
        identifier = email

    user = query.first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"未找到用户: {identifier}"
        )

    return {
        "id": user.user_id,
        "username": user.user_name,
        "email": user.user_email,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
