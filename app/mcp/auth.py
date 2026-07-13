"""
MCP Server Authentication.

Provides JWT validation as ASGI middleware for MCP server SSE transport.
"""

from typing import Optional

from app.core.security import decode_access_token


def decode_user_id_from_token(token: str) -> Optional[int]:
    """Validate a JWT access token and return the user_id (sub claim)."""
    payload = decode_access_token(token)
    if payload is None:
        return None
    try:
        return int(payload.get("sub"))
    except (TypeError, ValueError):
        return None


def require_bearer_auth(app, exclude_paths: set[str] | None = None):
    """
    ASGI middleware wrapper that requires a valid JWT Bearer token.

    Usage with MCP SSE app::

        from app.mcp.auth import require_bearer_auth

        app = mcp.sse_app()
        app = require_bearer_auth(app)
        uvicorn.run(app, host="0.0.0.0", port=8001)

    Parameters
    ----------
    app : ASGI app
        The underlying ASGI app to protect.
    exclude_paths : set[str], optional
        Path prefixes to exclude from auth checks.

    Returns
    -------
    ASGI app with auth middleware.
    """
    if exclude_paths is None:
        exclude_paths = set()

    async def auth_middleware(scope, receive, send):
        # Only intercept HTTP requests
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in exclude_paths):
            await app(scope, receive, send)
            return

        # Parse headers
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        if not token:
            await _send_401(
                send, "Missing Authorization header (Bearer token required)"
            )
            return

        user_id = decode_user_id_from_token(token)
        if user_id is None:
            await _send_401(send, "Invalid or expired token")
            return

        # Inject user_id into scope for downstream use
        scope["mcp_user_id"] = user_id
        await app(scope, receive, send)

    return auth_middleware


async def _send_401(send, detail: str):
    body = f'{{"error":"Unauthorized","message":"{detail}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
