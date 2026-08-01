"""MCP OAuth 2.1 discovery 端点 —— Protected Resource Metadata (RFC 9728) 与
Authorization Server Metadata (RFC 8414)，用于 ChatGPT 等 OAuth 客户端自动发现配置。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["OAuth Discovery"])

# --- 统一用该域名作为 resource / issuer ---
CANONICAL_ORIGIN = "https://incremental.icu"


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """RFC 9728 Protected Resource Metadata — 告诉客户端在哪里找授权服务器。"""
    return JSONResponse(
        {
            "resource": CANONICAL_ORIGIN,
            "authorization_servers": [CANONICAL_ORIGIN],
            "scopes_supported": ["read"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://incremental.icu/docs",
        }
    )


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414 Authorization Server Metadata — 告诉客户端 OAuth 端点位置与能力。"""
    return JSONResponse(
        {
            "issuer": CANONICAL_ORIGIN,
            "authorization_endpoint": f"{CANONICAL_ORIGIN}/oauth/authorize",
            "token_endpoint": f"{CANONICAL_ORIGIN}/oauth/token",
            # OpenAI/ChatGPT 通过 DCR 动态注册客户端，缺少该字段会报
            # "MCP server does not implement OAuth"
            "registration_endpoint": f"{CANONICAL_ORIGIN}/oauth/register",
            "registration_endpoint_auth_methods_supported": ["none"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["read"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
        }
    )
