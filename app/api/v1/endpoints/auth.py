from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
import random
import requests
import resend

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.models.user_verify_code import UserVerifyCode
from app.models.refresh_token import UserRefreshToken
from app.models.user_social import UserSocial
from app.core.security import create_access_token, create_refresh_token

router = APIRouter()

class GoogleLoginRequest(BaseModel):
    email: str
    name: str
    avatar: Optional[str]
    googleId: str
    idToken: str
    accessToken: Optional[str] = None
    refreshToken: Optional[str] = None


class GitHubLoginRequest(BaseModel):
    email: str
    name: str
    avatar: Optional[str]
    githubId: str
    accessToken: str
    refreshToken: Optional[str] = None


def verify_google_id_token(id_token: str, google_id: str, email: str):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google Client ID 未配置")

    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Google ID Token 校验失败: {str(exc)}")

    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=400, detail="idToken 的客户端 ID 不匹配")

    if payload.get("sub") != google_id:
        raise HTTPException(status_code=400, detail="googleId 与 idToken 不匹配")

    if payload.get("email") != email:
        raise HTTPException(status_code=400, detail="idToken 中的邮箱与请求邮箱不匹配")

    if payload.get("email_verified") not in (True, "true", "True"):
        raise HTTPException(status_code=400, detail="Google 账户邮箱未验证")


def verify_github_access_token(access_token: str, github_id: str, email: str):
    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}"},
            timeout=5,
        )
        response.raise_for_status()
        user_data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"GitHub Access Token 校验失败: {str(exc)}")

    if str(user_data.get("id")) != github_id:
        raise HTTPException(status_code=400, detail="githubId 与 Access Token 不匹配")

    # GitHub 可能不提供 email，如果提供则校验
    if user_data.get("email") and user_data.get("email") != email:
        raise HTTPException(status_code=400, detail="Access Token 中的邮箱与请求邮箱不匹配")

    return user_data


def exchange_github_code(code: str) -> str:
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub 客户端配置未配置")

    try:
        response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
            },
            timeout=5,
        )
        response.raise_for_status()
        token_data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"GitHub code 兑换失败: {str(exc)}")

    if token_data.get("error"):
        raise HTTPException(status_code=400, detail=token_data.get("error_description") or token_data.get("error"))

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub 未返回 access_token")

    return access_token


def fetch_github_user_info(access_token: str) -> dict:
    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}", "Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        response.raise_for_status()
        user_data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"GitHub 用户信息获取失败: {str(exc)}")

    if not user_data.get("email"):
        try:
            email_response = requests.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"token {access_token}", "Accept": "application/vnd.github.v3+json"},
                timeout=5,
            )
            email_response.raise_for_status()
            emails = email_response.json()
            primary_email = next(
                (item.get("email") for item in emails if item.get("primary") and item.get("verified")),
                None,
            )
            if primary_email:
                user_data["email"] = primary_email
        except requests.RequestException:
            pass

    if not user_data.get("email"):
        raise HTTPException(status_code=400, detail="GitHub 未提供有效邮箱，请检查授权范围")

    return user_data


def generate_unique_username(db: Session, base_name: str, email: str) -> str:
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

# --- 辅助方法：统一验证码校验逻辑 ---

# --- 辅助方法：统一验证码校验逻辑 ---
def verify_captcha_logic(db: Session, email: str, code: str, purpose: str):
    now = datetime.now(timezone.utc)
    db_code = db.query(UserVerifyCode).filter(
        UserVerifyCode.email == email,
        UserVerifyCode.code == code,
        UserVerifyCode.purpose == purpose,
        UserVerifyCode.used == False,
        UserVerifyCode.expires_at > now
    ).first()
    
    if not db_code:
        raise HTTPException(status_code=400, detail="验证码无效、已过期或已使用")
    
    # 标记验证码已使用
    db_code.used = True
    return db_code

# --- 1. 注册接口 ---
@router.post("/register")
def register(
    username: str, 
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    # A. 校验用户名和邮箱是否已存在
    existing_user = db.query(User).filter(
        or_(User.user_name == username, User.user_email == email)
    ).first()
    
    if existing_user:
        field = "用户名" if existing_user.user_name == username else "邮箱"
        raise HTTPException(status_code=400, detail=f"该{field}已被注册")

    # B. 校验验证码（purpose设为 'register'）
    verify_captcha_logic(db, email, captcha, "register")

    # C. 创建并激活用户
    now = datetime.now(timezone.utc)
    new_user = User(
        user_name=username,
        user_email=email,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(new_user)
    db.flush()

    # D. 生成 Tokens
    tokens = generate_user_tokens(db, new_user, request)
    
    db.commit()
    return tokens

# --- 2. 登录接口 ---
@router.post("/login")
def login(
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    # A. 校验验证码（purpose设为 'login'）
    verify_captcha_logic(db, email, captcha, "login")

    # B. 查找用户
    user = db.query(User).filter(User.user_email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱未注册，请先注册")
    
    if not user.active:
        raise HTTPException(status_code=400, detail="账号已被禁用")

    # C. 生成 Tokens
    tokens = generate_user_tokens(db, user, request)
    
    db.commit()
    return tokens

# --- 3. 封装 Token 生成逻辑 ---
def generate_user_tokens(db: Session, user: User, request: Request):
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

@router.post("/google-login")
def google_login(
    payload: GoogleLoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    # 1. 校验 Google idToken 与 googleId
    verify_google_id_token(payload.idToken, payload.googleId, payload.email)

    # 2. 查找用户：先按 provider_user_id，再按 email
    social = db.query(UserSocial).filter(
        UserSocial.provider == "google",
        UserSocial.provider_user_id == payload.googleId
    ).first()

    user = None
    now = datetime.now(timezone.utc)
    if social:
        user = db.query(User).filter(User.user_id == social.user_id).first()
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            # 可选更新 access token
            if payload.accessToken:
                social.access_token = payload.accessToken
    else:
        user = db.query(User).filter(User.user_email == payload.email).first()
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            social = UserSocial(
                user_id=user.user_id,
                provider="google",
                provider_user_id=payload.googleId,
                access_token=payload.accessToken,
                created_at=now,
            )
            db.add(social)
        else:
            username = generate_unique_username(db, payload.name, payload.email)
            user = User(
                user_name=username,
                user_email=payload.email,
                active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.flush()

            social = UserSocial(
                user_id=user.user_id,
                provider="google",
                provider_user_id=payload.googleId,
                access_token=payload.accessToken,
                created_at=now,
            )
            db.add(social)

    # 3. 生成系统 Tokens
    tokens = generate_user_tokens(db, user, request)
    db.commit()

    return {
        **tokens,
        "user": {
            "id": user.user_id,
            "username": user.user_name,
            "email": user.user_email,
        }
    }


@router.post("/github-login")
def github_login(
    payload: GitHubLoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    # 1. 校验 GitHub accessToken 与 githubId
    verify_github_access_token(payload.accessToken, payload.githubId, payload.email)

    # 2. 查找用户：先按 provider_user_id，再按 email
    social = db.query(UserSocial).filter(
        UserSocial.provider == "github",
        UserSocial.provider_user_id == payload.githubId
    ).first()

    user = None
    now = datetime.now(timezone.utc)
    if social:
        user = db.query(User).filter(User.user_id == social.user_id).first()
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            # 可选更新 access token
            social.access_token = payload.accessToken
    else:
        user = db.query(User).filter(User.user_email == payload.email).first()
        if user:
            if not user.active:
                raise HTTPException(status_code=400, detail="账号已被禁用")
            social = UserSocial(
                user_id=user.user_id,
                provider="github",
                provider_user_id=payload.githubId,
                access_token=payload.accessToken,
                created_at=now,
            )
            db.add(social)
        else:
            username = generate_unique_username(db, payload.name, payload.email)
            user = User(
                user_name=username,
                user_email=payload.email,
                active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.flush()

            social = UserSocial(
                user_id=user.user_id,
                provider="github",
                provider_user_id=payload.githubId,
                access_token=payload.accessToken,
                created_at=now,
            )
            db.add(social)

    # 3. 生成系统 Tokens
    tokens = generate_user_tokens(db, user, request)
    db.commit()

    return {
        **tokens,
        "user": {
            "id": user.user_id,
            "username": user.user_name,
            "email": user.user_email,
        }
    }


@router.get("/github/callback")
def github_callback(
    code: str,
    request: Request,
    db: Session = Depends(get_db)
):
    access_token = exchange_github_code(code)
    github_user = fetch_github_user_info(access_token)
    payload = GitHubLoginRequest(
        email=github_user["email"],
        name=github_user.get("name") or github_user.get("login") or "GitHub User",
        avatar=github_user.get("avatar_url"),
        githubId=str(github_user["id"]),
        accessToken=access_token,
    )
    return github_login(payload, request, db)


@router.post("/send-captcha")
def send_captcha(
    email: str, 
    purpose: str,
    request: Request, 
    db: Session = Depends(get_db)
):
    """
    生成验证码，存入数据库，并通过 Resend 发送邮件
    """
    if purpose == "register":
        existing_email = db.query(User).filter(User.user_email == email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="该邮箱已注册")

    code = f"{random.randint(100000, 999999)}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)

    # 构造数据库记录 (使用你的 UserVerifyCode 模型)
    db_captcha = UserVerifyCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=expires_at,
        created_at=now,
        used=False,
        ip_address=request.client.host
    )
    
    try:
        # 调用 Resend 发送邮件
        r = resend.Emails.send({
            "from": "Incremental <onboarding@resend.dev>",
            "to": [email],
            "subject": f"您的验证码是: {code}",
            "html": f"""
                <p>您好，</p>
                <p>您的验证码为：<strong>{code}</strong></p>
                <p>有效期为 5 分钟。如果您没有请求此代码，请忽略此邮件。</p>
            """
        })
        
        # 邮件发送成功后，将记录写入数据库
        db.add(db_captcha)
        db.commit()
        
        return {"message": "验证码已发送", "id": r["id"]}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"邮件发送失败: {str(e)}"
        )
@router.post("/refresh")
def refresh_token_endpoint(
    refresh_token: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    使用 Refresh Token 获取新的 Access Token。
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
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Refresh Token 无效或已过期"
        )

    # 检查关联用户是否存在且活跃
    user = db.query(User).filter(User.user_id == db_token.user_id).first()
    if not user or not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="用户不存在或已被禁用"
        )

    # 标记旧的 Refresh Token 为已撤回（Rotation 机制）
    db_token.revoked = True
    
    # 生成一套全新的 Tokens
    new_tokens = generate_user_tokens(db, user, request)
    
    db.commit()
    return new_tokens