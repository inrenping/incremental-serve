"""OAuth 服务（Google、GitHub）"""
import requests
from typing import Dict, Any
from fastapi import HTTPException
from app.core.config import settings


# ============ Google OAuth =============

def verify_google_token(token: str, google_id: str, email: str) -> None:
    """
    自适应验证 Google Token (支持 ID Token 和 Access Token)
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google Client ID 未配置")

    # 1. 自动判断 Token 类型并决定验证接口
    if token.startswith("ya29."):
        # 这是 Access Token
        verify_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        params = {}
        headers = {"Authorization": f"Bearer {token}"}
    else:
        # 假设是 ID Token (JWT)
        verify_url = "https://oauth2.googleapis.com/tokeninfo"
        params = {"id_token": token}
        headers = {}

    try:
        response = requests.get(verify_url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Google Token 校验失败: {str(exc)}")

    # 2. 字段名在不同接口中可能略有不同
    # Access Token 接口返回 'sub'，ID Token 接口也返回 'sub'
    remote_sub = payload.get("sub")
    remote_email = payload.get("email")

    if remote_sub != google_id:
        raise HTTPException(status_code=400, detail="googleId 与 Token 不匹配")

    if remote_email != email:
        raise HTTPException(status_code=400, detail="Token 中的邮箱不匹配")


# ============ GitHub OAuth =============

def verify_github_access_token(access_token: str, github_id: str, email: str) -> Dict[str, Any]:
    """
    验证 GitHub Access Token
    检查 token 有效性、用户ID、邮箱匹配情况
    """
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
    """
    用 GitHub Authorization Code 换取 Access Token
    """
    if not settings.GIT_HUB_CLIENT_ID or not settings.GIT_HUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub 客户端配置未配置")

    try:
        response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GIT_HUB_CLIENT_ID,
                "client_secret": settings.GIT_HUB_CLIENT_SECRET,
                "code": code,
            },
            timeout=5,
        )
        response.raise_for_status()
        token_data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"GitHub code 兑换失败: {str(exc)}")

    if token_data.get("error"):
        raise HTTPException(
            status_code=400,
            detail=token_data.get("error_description") or token_data.get("error")
        )

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub 未返回 access_token")

    return access_token


def fetch_github_user_info(access_token: str) -> Dict[str, Any]:
    """
    从 GitHub 获取用户信息和邮箱
    如果主请求中没有邮箱，尝试从 /user/emails 端点获取
    """
    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {access_token}",
                "Accept": "application/vnd.github.v3+json"
            },
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
                headers={
                    "Authorization": f"token {access_token}",
                    "Accept": "application/vnd.github.v3+json"
                },
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
