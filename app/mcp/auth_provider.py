"""
OAuth 2.0 Authorization Server Provider for MCP.

Integrates the MCP library's built-in OAuth support with
our existing database models and JWT infrastructure.
"""

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from pydantic import AnyHttpUrl, AnyUrl
from sqlalchemy.orm import Session

from app.core.security import create_access_token, decode_access_token
from app.db.session import SessionLocal
from app.models.oauth_code import OAuthAuthorizationCode
from app.models.refresh_token import UserRefreshToken
from app.models.user import User
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    AccessToken,
    TokenError,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
    AnyHttpUrl as AuthAnyHttpUrl,
)

# OAuth client configuration
CLIENT_CONFIG = {
    "gpt-actions": {
        "client_name": "ChatGPT GPT Actions",
        "redirect_uris": ["https://chatgpt.com/*", "https://chat.openai.com/*"],
        "scope": "read",
    }
}

MCP_LOGIN_BASE = "/mcp-auth"
MCP_AUTH_CODE_EXPIRE_SECONDS = 600  # 10 minutes
MCP_ACCESS_TOKEN_EXPIRE_DAYS = 365
MCP_REFRESH_TOKEN_EXPIRE_DAYS = 400


def _build_client() -> OAuthClientInformationFull:
    """Build the OAuthClientInformationFull for gpt-actions."""
    cfg = CLIENT_CONFIG["gpt-actions"]
    # Map wildcard redirect URIs to AnyUrl
    redirect_uris = []
    for uri in cfg["redirect_uris"]:
        if uri.endswith("*"):
            base_uri = uri.rstrip("*")
            redirect_uris.append(AnyUrl(f"{base_uri}oauth/callback"))
        else:
            redirect_uris.append(AnyUrl(uri))
    return OAuthClientInformationFull(
        client_id="gpt-actions",
        redirect_uris=redirect_uris,
        scope=cfg["scope"],
        grant_types=["authorization_code", "refresh_token"],
    )


class MCPAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider using our database models and JWT."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id == "gpt-actions":
            return _build_client()
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Dynamic client registration is not supported")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """
        Redirect the user to our login page.
        The login page will handle email+captcha authentication,
        show consent, generate authorization code, and redirect back.
        """
        # Encode params into the redirect URL so our login flow can use them
        query = urlencode(
            {
                "client_id": client.client_id or "gpt-actions",
                "redirect_uri": str(params.redirect_uri),
                "code_challenge": params.code_challenge,
                "state": params.state or "",
                "scope": " ".join(params.scopes) if params.scopes else "read",
            }
        )
        return f"{MCP_LOGIN_BASE}/login?{query}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        db: Session = SessionLocal()
        try:
            record = (
                db.query(OAuthAuthorizationCode)
                .filter(
                    OAuthAuthorizationCode.code == authorization_code,
                    OAuthAuthorizationCode.used == False,
                    OAuthAuthorizationCode.client_id == client.client_id,
                )
                .first()
            )
            if not record:
                return None
            return AuthorizationCode(
                code=record.code,
                client_id=record.client_id,
                scopes=[record.scope] if record.scope else [],
                expires_at=record.expires_at.timestamp(),
                code_challenge=record.code or "",
                redirect_uri=AnyUrl(record.redirect_uri or ""),
                redirect_uri_provided_explicitly=bool(record.redirect_uri),
                subject=str(record.user_id),
            )
        finally:
            db.close()

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        db: Session = SessionLocal()
        try:
            # Mark the code as used
            record = (
                db.query(OAuthAuthorizationCode)
                .filter(
                    OAuthAuthorizationCode.code == authorization_code.code,
                    OAuthAuthorizationCode.used == False,
                )
                .first()
            )
            if not record:
                raise TokenError("invalid_grant", "authorization code not found")

            record.used = True

            user = db.query(User).filter(User.id == record.user_id).first()
            if not user or not user.active:
                raise TokenError("invalid_grant", "user not found or disabled")

            # Issue long-lived access token
            access_token = create_access_token(
                data={"sub": str(user.id)},
                expires_delta=timedelta(days=MCP_ACCESS_TOKEN_EXPIRE_DAYS),
            )

            # Issue refresh token
            refresh_token_str = secrets.token_urlsafe(64)
            refresh_record = UserRefreshToken(
                user_id=user.id,
                refresh_token=refresh_token_str,
                expires_time=datetime.now(timezone.utc)
                + timedelta(days=MCP_REFRESH_TOKEN_EXPIRE_DAYS),
                created_at=datetime.now(timezone.utc),
                revoked=False,
            )
            db.add(refresh_record)
            db.commit()

            return OAuthToken(
                access_token=access_token,
                refresh_token=refresh_token_str,
                expires_in=MCP_ACCESS_TOKEN_EXPIRE_DAYS * 86400,
                token_type="bearer",
            )
        finally:
            db.close()

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        db: Session = SessionLocal()
        try:
            record = (
                db.query(UserRefreshToken)
                .filter(
                    UserRefreshToken.refresh_token == refresh_token,
                    UserRefreshToken.revoked == False,
                    UserRefreshToken.expires_time > datetime.now(timezone.utc),
                )
                .first()
            )
            if not record:
                return None
            return RefreshToken(
                token=record.refresh_token,
                client_id=client.client_id or "gpt-actions",
                scopes=["read"],
                expires_at=int(record.expires_time.timestamp()),
                subject=str(record.user_id),
            )
        finally:
            db.close()

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        db: Session = SessionLocal()
        try:
            record = (
                db.query(UserRefreshToken)
                .filter(
                    UserRefreshToken.refresh_token == refresh_token.token,
                    UserRefreshToken.revoked == False,
                )
                .first()
            )
            if not record:
                raise TokenError("invalid_grant", "refresh token not found")

            # Rotate: revoke old, issue new
            record.revoked = True

            user = db.query(User).filter(User.id == record.user_id).first()
            if not user or not user.active:
                raise TokenError("invalid_grant", "user not found or disabled")

            new_access_token = create_access_token(
                data={"sub": str(user.id)},
                expires_delta=timedelta(days=MCP_ACCESS_TOKEN_EXPIRE_DAYS),
            )

            new_refresh_token_str = secrets.token_urlsafe(64)
            new_refresh_record = UserRefreshToken(
                user_id=user.id,
                refresh_token=new_refresh_token_str,
                expires_time=datetime.now(timezone.utc)
                + timedelta(days=MCP_REFRESH_TOKEN_EXPIRE_DAYS),
                created_at=datetime.now(timezone.utc),
                revoked=False,
            )
            db.add(new_refresh_record)
            db.commit()

            return OAuthToken(
                access_token=new_access_token,
                refresh_token=new_refresh_token_str,
                expires_in=MCP_ACCESS_TOKEN_EXPIRE_DAYS * 86400,
                token_type="bearer",
            )
        finally:
            db.close()

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Verify a JWT access token and return access info."""
        payload = decode_access_token(token)
        if payload is None:
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        return AccessToken(
            token=token,
            client_id="gpt-actions",
            scopes=["read"],
            subject=str(user_id),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke a token."""
        db: Session = SessionLocal()
        try:
            if isinstance(token, RefreshToken):
                record = (
                    db.query(UserRefreshToken)
                    .filter(
                        UserRefreshToken.refresh_token == token.token,
                        UserRefreshToken.revoked == False,
                    )
                    .first()
                )
                if record:
                    record.revoked = True
                    db.commit()
        finally:
            db.close()
