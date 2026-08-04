"""OAuth 授权验证码服务。

用户登录主站后，在 /dash/gpt 页面查看 6 位验证码，
在 OpenAI / ChatGPT 的 OAuth 授权页直接输入该验证码即可完成授权，
不再依赖邮件验证码。
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.oauth_verify_code import OAuthVerifyCode
from app.models.user import User

OAUTH_CODE_TTL_SECONDS = 300  # 验证码有效期 5 分钟
OAUTH_CODE_MAX_PER_MINUTE = 3  # 同一用户每分钟最多生成 3 次（限流）


def _generate_code() -> str:
    """生成 6 位随机数字验证码"""
    return f"{random.randint(100000, 999999)}"


def get_or_create_code(
    db: Session, user: User, force: bool = False
) -> dict:
    """
    获取当前用户的有效 OAuth 验证码；没有则生成新的。

    Args:
        db: 数据库会话
        user: 当前登录用户
        force: 为 True 时强制轮换一个新验证码（旧的立即作废）

    Returns:
        {"code": str, "expires_at": datetime, "expires_in": int}
    """
    now = datetime.now(timezone.utc)

    if not force:
        active = (
            db.query(OAuthVerifyCode)
            .filter(
                OAuthVerifyCode.user_id == user.id,
                OAuthVerifyCode.used == False,
                OAuthVerifyCode.expires_at > now,
            )
            .order_by(OAuthVerifyCode.id.desc())
            .first()
        )
        if active:
            expires_in = max(
                0, int((active.expires_at - now).total_seconds())
            )
            return {
                "code": active.code,
                "expires_at": active.expires_at,
                "expires_in": expires_in,
            }

    # 限流：同一用户 1 分钟内最多生成 3 次
    recent_count = (
        db.query(OAuthVerifyCode)
        .filter(
            OAuthVerifyCode.user_id == user.id,
            OAuthVerifyCode.created_at >= now - timedelta(minutes=1),
        )
        .count()
    )
    if recent_count >= OAUTH_CODE_MAX_PER_MINUTE:
        raise HTTPException(
            status_code=429, detail="验证码生成过于频繁，请稍后再试"
        )

    # 强制轮换时，作废该用户所有未使用的旧验证码
    if force:
        db.query(OAuthVerifyCode).filter(
            OAuthVerifyCode.user_id == user.id,
            OAuthVerifyCode.used == False,
        ).update({"used": True})

    code = _generate_code()
    expires_at = now + timedelta(seconds=OAUTH_CODE_TTL_SECONDS)
    record = OAuthVerifyCode(
        user_id=user.id,
        code=code,
        expires_at=expires_at,
        used=False,
        created_at=now,
    )
    db.add(record)
    db.commit()

    return {
        "code": code,
        "expires_at": expires_at,
        "expires_in": OAUTH_CODE_TTL_SECONDS,
    }


def verify_code(db: Session, code: str) -> OAuthVerifyCode:
    """
    校验用户输入的验证码并核销（一次性使用）。

    Args:
        db: 数据库会话
        code: 用户输入的 6 位验证码

    Returns:
        命中且已标记为已使用的验证码记录（含 user_id）

    Raises:
        HTTPException 400: 验证码无效、已过期或已使用
    """
    now = datetime.now(timezone.utc)
    record = (
        db.query(OAuthVerifyCode)
        .filter(
            OAuthVerifyCode.code == code,
            OAuthVerifyCode.used == False,
            OAuthVerifyCode.expires_at > now,
        )
        .order_by(OAuthVerifyCode.id.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=400, detail="验证码无效、已过期或已使用")

    record.used = True
    db.commit()
    return record
