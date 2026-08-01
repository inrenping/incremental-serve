"""OAuth 2.1 Authorization Code + PKCE flow for MCP / GPT Actions integration."""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.oauth_code import OAuthAuthorizationCode
from app.models.user import User
from app.models.refresh_token import UserRefreshToken
from app.core.security import create_access_token, create_refresh_token

router = APIRouter(prefix="/oauth", tags=["OAuth"])

# Built-in OAuth client for GPT Actions / ChatGPT MCP
OAUTH_CLIENTS = {
    "gpt-actions": {
        "client_name": "ChatGPT GPT Actions / MCP",
        "redirect_uris": ["https://chatgpt.com/*", "https://chat.openai.com/*"],
    }
}

# 动态注册的 OAuth 客户端（RFC 7591 DCR）—— OpenAI/ChatGPT 连接时自动注册。
# 使用进程内存储即可满足注册→授权→换 token 的短生命周期流程。
_REGISTERED_CLIENTS: dict[str, dict] = {}

OAUTH_ACCESS_TOKEN_EXPIRE_DAYS = 365
OAUTH_AUTH_CODE_EXPIRE_MINUTES = 10
OAUTH_REFRESH_TOKEN_EXPIRE_DAYS = 400


class TokenRequest(BaseModel):
    grant_type: str = "authorization_code"
    code: str
    redirect_uri: str | None = None
    client_id: str | None = None
    code_verifier: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None


class ClientRegistrationRequest(BaseModel):
    client_name: str | None = None
    redirect_uris: list[str] | None = None
    # 部分客户端可能用单数字段名或动态回调 URL（OpenAI 回调是动态生成的）
    redirect_uri: str | None = None
    callback_url: str | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    scope: str | None = None
    token_endpoint_auth_method: str | None = None


# OpenAI/ChatGPT 动态回调 URL 的域名白名单（回调由 ChatGPT 每个连接器动态生成）
_OPENAI_REDIRECT_PATTERNS = [
    "https://chatgpt.com/",
    "https://chat.openai.com/",
    "https://openai.com/",
    "https://apps-api.openai.com/",
    "https://platform.openai.com/",
]


class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    redirect_uris: list[str]
    token_endpoint_auth_method: str = "none"
    grant_types: list[str]
    response_types: list[str]
    scope: str


def _verify_pkce(code_verifier: str, code_challenge: str, method: str = "S256") -> bool:
    """Verify a PKCE code_verifier against the stored code_challenge."""
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


# ---------- HTML pages ----------

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incremental - 授权登录</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
        .card {{ background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); width: 360px; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #1a1a1a; }}
        p {{ color: #666; margin-bottom: 24px; font-size: 14px; }}
        label {{ display: block; margin-bottom: 6px; font-size: 14px; color: #333; font-weight: 500; }}
        input {{ width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box; margin-bottom: 16px; }}
        input:focus {{ outline: none; border-color: #4A90D9; }}
        .btn {{ width: 100%; padding: 12px; background: #4A90D9; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }}
        .btn:hover {{ background: #357ABD; }}
        .error {{ color: #e74c3c; font-size: 13px; margin-bottom: 12px; display: none; }}
        .captcha-row {{ display: flex; gap: 8px; }}
        .captcha-row input {{ flex: 1; }}
        .captcha-btn {{ padding: 10px 12px; background: #e8e8e8; border: 1px solid #ddd; border-radius: 8px; cursor: pointer; white-space: nowrap; font-size: 13px; }}
        .captcha-btn:hover {{ background: #ddd; }}
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
            <input type="hidden" name="code_challenge" value="{code_challenge}">
            <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
            <input type="hidden" name="resource" value="{resource}">
            <input type="hidden" name="state" value="{state}">
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
            // send-captcha 端点使用 query 参数（与主站一致），不能发 JSON body
            fetch('/api/v1/auth/send-captcha?email=' + encodeURIComponent(email) + '&purpose=login', {{
                method: 'POST'
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
                <input type="hidden" name="code_challenge" value="{code_challenge}">
                <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
                <input type="hidden" name="resource" value="{resource}">
                <input type="hidden" name="state" value="{state}">
                <button type="submit" name="action" value="deny" class="btn btn-deny">拒绝</button>
            </form>
            <form method="post" action="/oauth/consent" style="flex:1;">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="scope" value="{scope}">
                <input type="hidden" name="user_id" value="{user_id}">
                <input type="hidden" name="token" value="{token}">
                <input type="hidden" name="code_challenge" value="{code_challenge}">
                <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
                <input type="hidden" name="resource" value="{resource}">
                <input type="hidden" name="state" value="{state}">
                <button type="submit" name="action" value="allow" class="btn btn-allow">允许</button>
            </form>
        </div>
    </div>
</body>
</html>"""


def _validate_redirect_uri(client_id: str, redirect_uri: str | None) -> str:
    """Validate redirect_uri against the client's allowed URIs."""
    client = OAUTH_CLIENTS.get(client_id) or _REGISTERED_CLIENTS.get(client_id)

    # 多 worker 部署下，进程内的动态注册记录可能落在其他 worker 上；
    # OpenAI 回调又是每个连接器动态生成的。对 DCR 客户端（dcr_ 前缀）做
    # 宽松校验：回调域名必须在 OpenAI 官方白名单内。
    if client is None and client_id.startswith("dcr_"):
        if redirect_uri and any(
            redirect_uri.startswith(p) for p in _OPENAI_REDIRECT_PATTERNS
        ):
            return redirect_uri

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


def _build_redirect_error(
    redirect_uri: str, error: str, state: str | None = None
) -> RedirectResponse:
    """Build a redirect response with error and optional state."""
    params = {"error": error}
    if state:
        params["state"] = state
    qs = urlencode(params)
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{qs}")


# ---------- Endpoints ----------


@router.post("/register")
def register_client(req: ClientRegistrationRequest):
    """RFC 7591 Dynamic Client Registration —— OpenAI/ChatGPT 连接 MCP 时自动注册。

    OpenAI 的插件连接器要求授权服务器支持 DCR（或 CIMD），
    否则会报 "MCP server does not implement OAuth"。
    """
    # 兼容各种客户端：redirect_uris 数组、redirect_uri 单数、callback_url，
    # 或完全不提供（OpenAI 的回调 URL 是动态生成的，注册阶段可能没有）
    redirect_uris = req.redirect_uris or []
    if not redirect_uris and req.redirect_uri:
        redirect_uris = [req.redirect_uri]
    if not redirect_uris and req.callback_url:
        redirect_uris = [req.callback_url]
    if not redirect_uris:
        # OpenAI 回调动态生成且域名在官方白名单内，注册时允许后续按域名校验
        redirect_uris = _OPENAI_REDIRECT_PATTERNS

    grant_types = req.grant_types or ["authorization_code", "refresh_token"]
    client_id = f"dcr_{secrets.token_urlsafe(16)}"
    _REGISTERED_CLIENTS[client_id] = {
        "client_name": req.client_name,
        "redirect_uris": redirect_uris,
        "scope": req.scope or "read",
        "grant_types": grant_types,
    }
    return ClientRegistrationResponse(
        client_id=client_id,
        client_id_issued_at=int(datetime.now(timezone.utc).timestamp()),
        redirect_uris=redirect_uris,
        token_endpoint_auth_method="none",
        grant_types=grant_types,
        response_types=["code"],
        scope=req.scope or "read",
    )


@router.get("/authorize", response_class=HTMLResponse)
def authorize_page(
    client_id: str,
    redirect_uri: str | None = None,
    scope: str | None = None,
    response_type: str | None = None,
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    resource: str | None = None,
    email: str | None = None,
    error: str | None = None,
):
    """Render the OAuth authorization login page (supports PKCE)."""
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
        code_challenge=code_challenge or "",
        code_challenge_method=code_challenge_method or "S256",
        resource=resource or "",
        state=state or "",
        email=email or "",
        error=f"<p>{error}</p>" if error else "",
    )
    return HTMLResponse(html)


@router.post("/authorize")
def authorize_login(
    request: Request,
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    scope: str = Form("read"),
    response_type: str = Form("code"),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
    resource: str = Form(""),
    state: str = Form(""),
    email: str = Form(""),
    captcha: str = Form(""),
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
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            state=state,
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
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            state=state,
            error="验证码错误或已过期",
            email=email,
        )

    user = db.query(User).filter(User.user_email == email).first()
    if not user or not user.active:
        return authorize_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            state=state,
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
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
        state=state,
    )
    return HTMLResponse(html)


@router.post("/consent")
def authorize_consent(
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    scope: str = Form("read"),
    user_id: int = Form(0),
    token: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
    resource: str = Form(""),
    state: str = Form(""),
    action: str = Form(""),
    db: Session = Depends(get_db),
):
    """Handle user consent (allow or deny), storing PKCE params with the auth code."""
    if action == "deny":
        return _build_redirect_error(redirect_uri, "access_denied", state)

    if action != "allow":
        return HTMLResponse("无效操作", status_code=400)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return HTMLResponse("用户不存在", status_code=400)

    # Generate authorization code with PKCE support
    code = secrets.token_urlsafe(32)
    auth_code = OAuthAuthorizationCode(
        user_id=user.id,
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge if code_challenge else None,
        code_challenge_method=code_challenge_method if code_challenge else None,
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=OAUTH_AUTH_CODE_EXPIRE_MINUTES),
    )
    db.add(auth_code)
    db.commit()

    # Redirect back to the client with code + state
    params = {"code": code}
    if state:
        params["state"] = state
    qs = urlencode(params)
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{qs}")


@router.post("/token", response_model=TokenResponse)
def exchange_token(
    grant_type: str = Form("authorization_code"),
    code: str = Form(...),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    code_verifier: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Exchange an authorization code (with PKCE verification) for access + refresh tokens.

    OAuth 2.0 规范要求 token 端点接受 application/x-www-form-urlencoded 表单
    （RFC 6749 §3.2），OpenAI/ChatGPT 等客户端均以表单方式 POST，而非 JSON。
    """
    now = datetime.now(timezone.utc)

    auth_code = (
        db.query(OAuthAuthorizationCode)
        .filter(
            OAuthAuthorizationCode.code == code,
            OAuthAuthorizationCode.used == False,
            OAuthAuthorizationCode.expires_at > now,
        )
        .first()
    )

    if not auth_code:
        raise HTTPException(status_code=400, detail="授权码无效或已过期")

    # PKCE verification — required when code_challenge was stored
    if auth_code.code_challenge:
        if not code_verifier:
            raise HTTPException(
                status_code=400, detail="缺少 code_verifier（PKCE 要求）"
            )
        if not _verify_pkce(
            code_verifier,
            auth_code.code_challenge,
            auth_code.code_challenge_method or "S256",
        ):
            raise HTTPException(status_code=400, detail="code_verifier 无效")

    # Mark code as used (one-time use)
    auth_code.used = True

    user = db.query(User).filter(User.id == auth_code.user_id).first()
    if not user or not user.active:
        raise HTTPException(status_code=400, detail="用户不存在或已被禁用")

    # Issue long-lived access token (365 days) — sub = user.id only
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
