"""认证服务"""
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.refresh_token import UserRefreshToken
from app.models.user_social import UserSocial
from app.core.security import create_access_token, create_refresh_token
from app.services.user_service import (
    get_user_by_email,
    get_active_user_by_email,
    create_user,
    generate_unique_username
)
from app.services.captcha_service import verify_captcha_logic


def generate_user_tokens(db: Session, user: User, request: Request) -> Dict[str, Any]:
    """
    为用户生成 Access Token 和 Refresh Token

    Returns:
        包含 access_token, refresh_token, token_type, user_id 的字典
    """
    # 生成 Access Token (通常 15-60 分钟)
    access_token = create_access_token(data={"sub": str(user.user_id)})

    # 生成 Refresh Token (通常 7 天)
    refresh_token_str = create_refresh_token()

    new_refresh_token = UserRefreshToken(
        user_id=user.user_id,
        refresh_token=refresh_token_str,
        expires_time=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
        expires_ip=request.client.host,
        user_agent=request.headers.get("user-agent"),
        revoked=False
    )
    db.add(new_refresh_token)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user_id": user.user_id
    }


def register_user(db: Session, username: str, email: str, captcha: str, request: Request) -> Dict[str, Any]:
    """
    用户注册逻辑

    Args:
        db: 数据库会话
        username: 用户名
        email: 邮箱
        captcha: 验证码
        request: 请求对象

    Returns:
        包含 token 信息的响应字典
    """
    # A. 检查用户名和邮箱是否已存在
    existing_user = db.query(User).filter(
        (User.user_name == username) | (User.user_email == email)
    ).first()

    if existing_user:
        field = "用户名" if existing_user.user_name == username else "邮箱"
        raise HTTPException(status_code=400, detail=f"该{field}已被注册")

    # B. 验证验证码（purpose='register'）
    verify_captcha_logic(db, email, captcha, "register")

    # C. 创建并激活用户
    new_user = create_user(db, username, email)

    # D. 生成 Tokens
    tokens = generate_user_tokens(db, new_user, request)

    db.commit()
    return tokens


def login_user(db: Session, email: str, captcha: str, request: Request) -> Dict[str, Any]:
    """
    用户登录逻辑

    Args:
        db: 数据库会话
        email: 邮箱
        captcha: 验证码
        request: 请求对象

    Returns:
        包含 token 信息的响应字典
    """
    # A. 验证验证码（purpose='login'）
    verify_captcha_logic(db, email, captcha, "login")

    # B. 查找用户
    user = get_active_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱未注册，请先注册")

    # C. 生成 Tokens
    tokens = generate_user_tokens(db, user, request)

    db.commit()
    return tokens


def handle_oauth_user(db: Session, provider: str, provider_user_id: str, email: str, name: str,
                     avatar: str = None, access_token: str = None, request: Request = None) -> tuple:
    """
    处理 OAuth 用户登录/注册的通用逻辑

    Args:
        db: 数据库会话
        provider: OAuth 提供商 ('google' 或 'github')
        provider_user_id: 提供商中的用户ID
        email: 用户邮箱
        name: 用户名称
        avatar: 用户头像 URL
        access_token: OAuth access token
        request: 请求对象

    Returns:
        (user, social) 元组
    """
    now = datetime.now(timezone.utc)

    # 1. 查找社交登录记录
    social = db.query(UserSocial).filter(
        UserSocial.provider == provider,
        UserSocial.provider_user_id == provider_user_id
    ).first()

    user = None

    if social:
        # 2. 如果存在社交账户，查找关联的用户
        user = db.query(User).filter(User.user_id == social.user_id).first()
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            # 更新 access token
            if access_token:
                social.access_token = access_token
    else:
        # 3. 如果不存在社交记录，尝试通过邮箱找用户
        user = get_user_by_email(db, email)
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            # 创建社交账户绑定
            social = UserSocial(
                user_id=user.user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                access_token=access_token,
                created_at=now,
            )
            db.add(social)
        else:
            # 4. 都没有则创建新用户和新社交账户
            username = generate_unique_username(db, name, email)
            user = create_user(db, username, email)

            social = UserSocial(
                user_id=user.user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                access_token=access_token,
                created_at=now,
            )
            db.add(social)

    return user, social


def refresh_user_token(db: Session, refresh_token: str, request: Request) -> Dict[str, Any]:
    """
    刷新用户Token

    Args:
        db: 数据库会话
        refresh_token: 刷新令牌
        request: 请求对象

    Returns:
        新的 token 信息
    """
    now = datetime.now(timezone.utc)

    # 在数据库中查找该 Refresh Token
    db_token = db.query(UserRefreshToken).filter(
        UserRefreshToken.refresh_token == refresh_token,
        UserRefreshToken.revoked == False,
        UserRefreshToken.expires_time > now
    ).first()

    if not db_token:
        raise HTTPException(
            status_code=401,
            detail="Refresh Token 无效或已过期"
        )

    # 检查关联用户是否存在且活跃
    user = db.query(User).filter(User.user_id == db_token.user_id).first()
    if not user or not user.active:
        raise HTTPException(
            status_code=401,
            detail="用户不存在或已被禁用"
        )

    # 标记旧的 Refresh Token 为撤销（Rotation 机制）
    db_token.revoked = True

    # 生成一套全新的 Tokens
    new_tokens = generate_user_tokens(db, user, request)

    db.commit()
    return new_tokens
