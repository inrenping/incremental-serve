"""OAuth 2.0 Authorization Code Flow endpoints for GPT Actions integration."""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.oauth_code import OAuthAuthorizationCode
from app.models.user import User
from app.models.refresh_token import UserRefreshToken
from app.core.security import create_access_token, create_refresh_token

router = APIRouter(prefix="/oauth", tags=["OAuth"])

# Built-in OAuth client for GPT Actions
OAUTH_CLIENTS = {
    "gpt-actions": {
        "client_name": "ChatGPT GPT Actions",
        "redirect_uris": ["https://chatgpt.com/*", "https://chat.openai.com/*"],
    }
}

OAUTH_ACCESS_TOKEN_EXPIRE_DAYS = 365
OAUTH_AUTH_CODE_EXPIRE_MINUTES = 10
OAUTH_REFRESH_TOKEN_EXPIRE_DAYS = 400


class TokenRequest(BaseModel):
    grant_type: str = "authorization_code"
    code: str
    redirect_uri: str | None = None
    client_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incremental - 授权登录</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); width: 360px; }
        h1 { font-size: 24px; margin-bottom: 8px; color: #1a1a1a; }
        p { color: #666; margin-bottom: 24px; font-size: 14px; }
        label { display: block; margin-bottom: 6px; font-size: 14px; color: #333; font-weight: 500; }
        input { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; margin-bottom: 16px; }
        input:focus { outline: none; border-color: #4A90D9; }
        .btn { width: 100%; padding: 12px; background: #4A90D9; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
        .btn:hover { background: #357ABD; }
        .error { color: #e74c3c; font-size: 13px; margin-bottom: 12px; display: none; }
        .captcha-row { display: flex; gap: 8px; }
        .captcha-row input { flex: 1; }
        .captcha-btn { padding: 10px 12px; background: #e8e8e8; border: 1px solid #ddd; border-radius: 8px; cursor: pointer; white-space: nowrap; font-size: 13px; }
        .captcha-btn:hover { background: #ddd; }
    </style>
</head>
<body>
    <div class="card">
        <h1>授权登录</h1>
        <p>为 {client_name} 授权访问你的运动数据</p>
        <div class="error" id="error">{error}</div>
        <form method="post" action="/oauth/authorize">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="scope" value="{scope}">
            <input type="hidden" name="response_type" value="code">
            <label>邮箱</label>
            <input type="email" name="email" placeholder="your@email.com" value="{email}" required>
            <label>验证码</label>
            <div class="captcha-row">
                <input type="text" name="captcha" placeholder="输入验证码" required>
                <button type="button" class="captcha-btn" onclick="sendCaptcha()">发送验证码</button>
            </div>
            <button type="submit" class="btn" style="margin-top:8px;">登录并授权</button>
        </form>
    </div>
    <script>
        function sendCaptcha() {{
            const email = document.querySelector('input[name="email"]').value;
            if (!email) {{ alert('请先输入邮箱'); return; }}
            fetch('/api/v1/auth/send-captcha', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ email: email, purpose: 'login' }})
            }}).then(r => {{
                if (r.ok) alert('验证码已发送到 ' + email);
                else alert('发送失败');
            }});
        }}
    </script>
</body>
</html>"""

CONSENT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incremental - 确认授权</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
        .card {{ background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); width: 360px; text-align: center; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #1a1a1a; }}
        p {{ color: #666; margin-bottom: 24px; font-size: 14px; }}
        .scope-list {{ text-align: left; background: #f9f9f9; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
        .scope-list li {{ margin: 8px 0; font-size: 14px; color: #333; }}
        .btn-group {{ display: flex; gap: 12px; }}
        .btn {{ flex: 1; padding: 12px; border-radius: 8px; font-size: 16px; cursor: pointer; border: none; }}
        .btn-allow {{ background: #4A90D9; color: white; }}
        .btn-allow:hover {{ background: #357ABD; }}
        .btn-deny {{ background: #e8e8e8; color: #666; }}
        .btn-deny:hover {{ background: #ddd; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>确认授权</h1>
        <p>你好 <strong>{user_name}</strong>，<strong>{client_name}</strong> 请求访问以下数据：</p>
        <div class="scope-list">
            <li>📊 运动活动数据（距离、时长、心率、配速等）</li>
            <li>❤️ 心率数据（每日汇总、采样明细）</li>
        </div>
        <div class="btn-group">
            <form method="post" action="/oauth/consent" style="flex:1;">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="scope" value="{scope}">
                <input type="hidden" name="user_id" value="{user_id}">
                <input type="hidden" name="token" value="{token}">
                <button type="submit" name="action" value="deny" class="btn btn-deny">拒绝</button>
            </form>
            <form method="post" action="/oauth/consent" style="flex:1;">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="scope" value="{scope}">
                <input type="hidden" name="user_id" value="{user_id}">
                <input type="hidden" name="token" value="{token}">
                <button type="submit" name="action" value="allow" class="btn btn-allow">允许</button>
            </form>
        </div>
    </div>
</body>
</html>"""


def _validate_redirect_uri(client_id: str, redirect_uri: str | None) -> str:
    """Validate redirect_uri against the client's allowed URIs."""
    client = OAUTH_CLIENTS.get(client_id)
    if not client:
        raise HTTPException(status_code=400, detail="未知的 client_id")

    if not redirect_uri:
        return client["redirect_uris"][0]

    allowed = client["redirect_uris"]
    for pattern in allowed:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if redirect_uri.startswith(prefix):
                return redirect_uri
        elif redirect_uri == pattern:
            return redirect_uri

    raise HTTPException(status_code=400, detail="redirect_uri 不在允许列表中")


def _build_redirect_error(redirect_uri: str, error: str) -> RedirectResponse:
    """Build a redirect response with an error parameter."""
    params = urlencode({"error": error})
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{params}")


@router.get("/authorize", response_class=HTMLResponse)
def authorize_page(
    client_id: str,
    redirect_uri: str | None = None,
    scope: str | None = None,
    response_type: str | None = None,
    state: str | None = None,
    email: str | None = None,
    error: str | None = None,
):
    """Render the OAuth authorization login page."""
    if response_type and response_type != "code":
        return HTMLResponse("不支持的 response_type", status_code=400)

    try:
        _validate_redirect_uri(client_id, redirect_uri)
    except HTTPException as e:
        return HTMLResponse(str(e.detail), status_code=400)

    client_name = OAUTH_CLIENTS.get(client_id, {}).get("client_name", client_id)

    html = LOGIN_PAGE.format(
        client_name=client_name,
        client_id=client_id,
        redirect_uri=redirect_uri or "",
        scope=scope or "read",
        email=email or "",
        error=f"<p>{error}</p>" if error else "",
    )
    return HTMLResponse(html)


@router.post("/authorize")
def authorize_login(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "read",
    response_type: str = "code",
    email: str = "",
    captcha: str = "",
    db: Session = Depends(get_db),
):
    """Handle login form submission from the authorize page."""
    try:
        _validate_redirect_uri(client_id, redirect_uri)
    except HTTPException as e:
        return HTMLResponse(str(e.detail), status_code=400)

    if not email or not captcha:
        return authorize_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            error="请填写邮箱和验证码",
            email=email,
        )

    from app.services.captcha_service import verify_captcha_logic

    try:
        verify_captcha_logic(db, email, captcha, "login")
    except Exception:
        return authorize_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            error="验证码错误或已过期",
            email=email,
        )

    user = db.query(User).filter(User.user_email == email).first()
    if not user or not user.active:
        return authorize_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            error="用户不存在或已被禁用",
            email=email,
        )

    # Generate a one-time consent token (short-lived, not stored in DB)
    consent_token = secrets.token_urlsafe(32)

    html = CONSENT_PAGE.format(
        client_name=OAUTH_CLIENTS.get(client_id, {}).get("client_name", client_id),
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        user_name=user.user_name or user.user_email,
        user_id=user.id,
        token=consent_token,
    )
    return HTMLResponse(html)


@router.post("/consent")
def authorize_consent(
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "read",
    user_id: int = 0,
    token: str = "",
    action: str = "",
    db: Session = Depends(get_db),
):
    """Handle user consent (allow or deny)."""
    if action == "deny":
        return _build_redirect_error(redirect_uri, "access_denied")

    if action != "allow":
        return HTMLResponse("无效操作", status_code=400)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return HTMLResponse("用户不存在", status_code=400)

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    auth_code = OAuthAuthorizationCode(
        user_id=user.id,
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=OAUTH_AUTH_CODE_EXPIRE_MINUTES),
    )
    db.add(auth_code)
    db.commit()

    # Redirect back to the client with the code
    params = urlencode({"code": code})
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{params}")


@router.post("/token", response_model=TokenResponse)
def exchange_token(
    req: TokenRequest,
    db: Session = Depends(get_db),
):
    """Exchange an authorization code for access and refresh tokens."""
    now = datetime.now(timezone.utc)

    auth_code = (
        db.query(OAuthAuthorizationCode)
        .filter(
            OAuthAuthorizationCode.code == req.code,
            OAuthAuthorizationCode.used == False,
            OAuthAuthorizationCode.expires_at > now,
        )
        .first()
    )

    if not auth_code:
        raise HTTPException(status_code=400, detail="授权码无效或已过期")

    # Mark code as used (one-time use)
    auth_code.used = True

    user = db.query(User).filter(User.id == auth_code.user_id).first()
    if not user or not user.active:
        raise HTTPException(status_code=400, detail="用户不存在或已被禁用")

    # Issue long-lived access token (365 days)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(days=OAUTH_ACCESS_TOKEN_EXPIRE_DAYS),
    )

    # Issue refresh token (400 days)
    refresh_token_str = create_refresh_token()
    refresh_record = UserRefreshToken(
        user_id=user.id,
        refresh_token=refresh_token_str,
        expires_time=now + timedelta(days=OAUTH_REFRESH_TOKEN_EXPIRE_DAYS),
        created_at=now,
        revoked=False,
    )
    db.add(refresh_record)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=OAUTH_ACCESS_TOKEN_EXPIRE_DAYS * 86400,
        refresh_token=refresh_token_str,
        scope=auth_code.scope or "read",
    )
