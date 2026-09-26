"""MCP OAuth 2.1 discovery 端点 —— Protected Resource Metadata (RFC 9728) 与
Authorization Server Metadata (RFC 8414)，用于 ChatGPT 等 OAuth 客户端自动发现配置。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter(tags=["OAuth Discovery"])

# --- 站点 canonical 域名 ---
CANONICAL_ORIGIN = settings.CANONICAL_ORIGIN
# --- MCP 服务的 resource 标识（RFC 8707）---
# 必须与 incremental-mcp 侧的 mcp_resource 完全一致，且与令牌 aud 同值。
# 根路径元数据原本返回 CANONICAL_ORIGIN，与 /mcp 下的元数据不一致，
# 会破坏 RFC 8707 的资源匹配，这里统一为 MCP 端点 URI。
MCP_RESOURCE_URI = settings.MCP_RESOURCE_URI


def _protected_resource_metadata(resource: str) -> JSONResponse:
    return JSONResponse(
        {
            "resource": resource,
            "authorization_servers": [CANONICAL_ORIGIN],
            "scopes_supported": ["read"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://incremental.icu/docs",
        }
    )


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """RFC 9728 Protected Resource Metadata — 告诉客户端在哪里找授权服务器。

    根路径发现的客户端同样需要拿到 MCP endpoint 作为 resource，
    以便与 401 的 WWW-Authenticate 所指向的 resource 保持一致。
    """
    return _protected_resource_metadata(MCP_RESOURCE_URI)


@router.get("/.well-known/oauth-protected-resource/{path:path}")
def protected_resource_metadata_path(path: str):
    """RFC 9728 Path-based PRM — MCP 客户端会尝试
    {origin}/.well-known/oauth-protected-resource{mcp_path}（如 .../mcp）。
    resource 字段需与 MCP 服务 URL 一致。
    """
    resource = f"{CANONICAL_ORIGIN}/{path}".rstrip("/")
    # 对 MCP 路径直接返回统一常量，避免大小写/尾斜杠差异导致不一致
    if resource.rstrip("/") == CANONICAL_ORIGIN + "/mcp":
        resource = MCP_RESOURCE_URI
    return _protected_resource_metadata(resource)


def _authorization_server_metadata() -> dict:
    """RFC 8414 Authorization Server Metadata — 告诉客户端 OAuth 端点位置与能力。"""
    return {
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


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414 Authorization Server Metadata — 告诉客户端 OAuth 端点位置与能力。"""
    return JSONResponse(_authorization_server_metadata())


@router.get("/.well-known/openid-configuration")
def openid_configuration():
    """OpenID Connect Discovery — OpenAI/ChatGPT 可能用 OIDC 方式发现 OAuth 配置。

    与 RFC 8414 返回同一份 OAuth 元数据；OIDC 仅多了可选字段，这里一并补齐，
    避免 OpenAI 在两种发现路径中任选其一时 404。
    """
    meta = _authorization_server_metadata()
    meta.update(
        {
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256", "HS256"],
            "claims_supported": ["sub"],
            "userinfo_endpoint": "",
            "response_modes_supported": ["query"],
        }
    )
    return JSONResponse(meta)
