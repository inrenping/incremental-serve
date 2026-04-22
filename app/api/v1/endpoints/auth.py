from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.auth_service import register_user, login_user, handle_oauth_user, refresh_user_token, generate_user_tokens
from app.services.oauth_service import verify_google_token, verify_github_access_token, exchange_github_code, fetch_github_user_info
from app.services.captcha_service import create_and_send_captcha

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


@router.post("/register")
def register(
    username: str, 
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """用户注册"""
    return register_user(db, username, email, captcha, request)


# --- 2. 登录接口 ---
@router.post("/login")
def login(
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """用户登录"""
    return login_user(db, email, captcha, request)

@router.post("/google-login")
def google_login(
    payload: GoogleLoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Google OAuth 登录"""
    # 优先使用 idToken，如果没有则使用 accessToken
    token_to_verify = payload.idToken or payload.accessToken
    verify_google_token(token_to_verify, payload.googleId, payload.email)

    # 2. 处理 OAuth 用户（自动创建或绑定账户）
    user, social = handle_oauth_user(
        db=db,
        provider="google",
        provider_user_id=payload.googleId,
        email=payload.email,
        name=payload.name,
        avatar=payload.avatar,
        access_token=payload.accessToken,
        idToken=payload.idToken,
        request=request
    )

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
    """GitHub OAuth 登录"""
    # 1. 校验 GitHub accessToken 与 githubId
    verify_github_access_token(payload.accessToken, payload.githubId, payload.email)

    # 2. 处理 OAuth 用户（自动创建或绑定账户）
    user, social = handle_oauth_user(
        db=db,
        provider="github",
        provider_user_id=payload.githubId,
        email=payload.email,
        name=payload.name,
        avatar=payload.avatar,
        access_token=payload.accessToken,
        request=request
    )

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

@router.post("/github-login-by-code")
def github_login_by_code(
    code: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    专门给前端 callback 页面调用的接口
    接收前端传来的 code，换取用户信息并返回 JSON
    """
    # 1. 用 code 换 GitHub 的 access_token
    # 必须是 https://i.incremental.icu/login/callback
    github_access_token = exchange_github_code(code) 
    
    # 2. 获取 GitHub 用户信息
    github_user = fetch_github_user_info(github_access_token)
    
    # 3. 构造 LoginRequest（复用你之前的 Pydantic 模型）
    payload = GitHubLoginRequest(
        email=github_user["email"],
        name=github_user.get("name") or github_user.get("login") or "GitHub User",
        avatar=github_user.get("avatar_url"),
        githubId=str(github_user["id"]),
        accessToken=github_access_token,
    )
    
    # 4. 直接调用你写好的 github_login 逻辑
    # 它会处理 handle_oauth_user 和 generate_user_tokens
    # 最终返回类似 {"access_token": "...", "user": {...}} 的 JSON
    return github_login(payload, request, db)

@router.get("/github/callback")
def github_callback(
    code: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """GitHub OAuth 回调"""
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
    """生成验证码，存入数据库，并通过 Resend 发送邮件"""
    return create_and_send_captcha(db, email, purpose, request.client.host)

@router.post("/refresh")
def refresh_token_endpoint(
    refresh_token: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """使用 Refresh Token 获取新的 Access Token"""
    return refresh_user_token(db, refresh_token, request)