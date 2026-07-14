"""
MCP OAuth login and consent pages.

These handle the user-facing part of the OAuth Authorization Code flow
for ChatGPT MCP Plugin integration.
"""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.oauth_code import OAuthAuthorizationCode
from app.models.user import User

router = APIRouter(prefix="/mcp-auth", tags=["MCP Auth"])

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
        .error { color: #e74c3c; font-size: 13px; margin-bottom: 12px; }
        .captcha-row { display: flex; gap: 8px; }
        .captcha-row input { flex: 1; }
        .captcha-btn { padding: 10px 12px; background: #e8e8e8; border: 1px solid #ddd; border-radius: 8px; cursor: pointer; white-space: nowrap; font-size: 13px; }
        .captcha-btn:hover { background: #ddd; }
    </style>
</head>
<body>
    <div class="card">
        <h1>授权登录</h1>
        <p>为 <strong>ChatGPT GPT Actions</strong> 授权访问你的运动数据</p>
        <div class="error">{error}</div>
        <form method="post" action="/mcp-auth/login">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="code_challenge" value="{code_challenge}">
            <input type="hidden" name="state" value="{state}">
            <input type="hidden" name="scope" value="{scope}">
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
        <p>你好 <strong>{user_name}</strong>，授权 <strong>ChatGPT GPT Actions</strong> 访问以下数据：</p>
        <div class="scope-list">
            <li>运动活动数据（距离、时长、心率、配速等）</li>
            <li>心率数据（每日汇总、采样明细）</li>
        </div>
        <div class="btn-group">
            <form method="post" action="/mcp-auth/consent" style="flex:1;">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="code_challenge" value="{code_challenge}">
                <input type="hidden" name="state" value="{state}">
                <input type="hidden" name="scope" value="{scope}">
                <input type="hidden" name="user_id" value="{user_id}">
                <button type="submit" name="action" value="deny" class="btn btn-deny">拒绝</button>
            </form>
            <form method="post" action="/mcp-auth/consent" style="flex:1;">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="code_challenge" value="{code_challenge}">
                <input type="hidden" name="state" value="{state}">
                <input type="hidden" name="scope" value="{scope}">
                <input type="hidden" name="user_id" value="{user_id}">
                <button type="submit" name="action" value="allow" class="btn btn-allow">允许</button>
            </form>
        </div>
    </div>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
def login_page(
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    state: str = "",
    scope: str = "read",
    email: str = "",
    error: str = "",
):
    """Render the MCP OAuth login page."""
    html = LOGIN_PAGE.format(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scope=scope,
        email=email,
        error=f"<p>{error}</p>" if error else "",
    )
    return HTMLResponse(html)


@router.post("/login")
def login_submit(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    state: str = "",
    scope: str = "read",
    email: str = "",
    captcha: str = "",
    db: Session = Depends(get_db),
):
    """Handle login form submission."""
    if not email or not captcha:
        return login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            scope=scope,
            email=email,
            error="请填写邮箱和验证码",
        )

    from app.services.captcha_service import verify_captcha_logic

    try:
        verify_captcha_logic(db, email, captcha, "login")
    except Exception:
        return login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            scope=scope,
            email=email,
            error="验证码错误或已过期",
        )

    user = db.query(User).filter(User.user_email == email).first()
    if not user or not user.active:
        return login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            scope=scope,
            email=email,
            error="用户不存在或已被禁用",
        )

    # Show consent page
    from pydantic import AnyUrl
    html = CONSENT_PAGE.format(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scope=scope,
        user_name=user.user_name or user.user_email,
        user_id=user.id,
    )
    return HTMLResponse(html)


@router.post("/consent")
def consent_submit(
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    state: str = "",
    scope: str = "read",
    user_id: int = 0,
    action: str = "",
    db: Session = Depends(get_db),
):
    """Handle user consent (allow or deny)."""
    if action == "deny":
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(url=f"{redirect_uri}{separator}error=access_denied&state={state}")

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
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(auth_code)
    db.commit()

    # Redirect back to the client with the code
    from urllib.parse import urlencode
    params = urlencode({"code": code, "state": state}) if state else urlencode({"code": code})
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{params}")
