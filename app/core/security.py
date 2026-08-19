import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional, TYPE_CHECKING
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from jose.backends import rsa_backend
from jose.utils import long_to_bytes
from sqlalchemy.orm import Session
import requests

# 导入配置和数据库依赖
from app.core.config import settings
from app.db.session import get_db

if TYPE_CHECKING:
    from app.models.user import User

from app.models.user import User

# 配置常量
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

security = HTTPBearer()

# --- Token 生成与解码 ---


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # jose 会自动校验 exp，这里手动校验也是双重保险
        return decoded_token
    except JWTError:
        return None


# --- 依赖项：获取当前用户 ---


def get_current_user(
    db: Session = Depends(get_db),
    token: HTTPAuthorizationCredentials = Depends(security),
) -> "User":
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # 使用统一配置的 SECRET_KEY
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()

    if user is None:
        raise credentials_exception
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户账户已禁用"
        )

    return user


# --- Clerk JWKS 客户端 ---


class _ClerkJWKClient:
    """从 Clerk 的 JWKS endpoint 拉取并缓存公钥，用于验证 Clerk JWT (RS256)。"""

    def __init__(self):
        self._jwks: dict = {}  # kid -> {n, e}
        self._expires_at: float = 0

    def _fetch_jwks(self):
        if settings.CLERK_ISSUER is None:
            return
        url = f"{settings.CLERK_ISSUER.rstrip('/')}/.well-known/jwks.json"
        try:
            resp = requests.get(url, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            self._jwks = {k["kid"]: k for k in data.get("keys", [])}
            self._expires_at = time.time() + 3600  # 缓存 1 小时
        except Exception:
            pass

    def get_public_key(self, kid: str):
        if time.time() >= self._expires_at or kid not in self._jwks:
            self._fetch_jwks()
        jwk = self._jwks.get(kid)
        if jwk is None:
            return None
        # JWK RSA 参数 -> cryptography RSA public key
        n = int.from_bytes(long_to_bytes(jwk["n"]), "big")
        e = int.from_bytes(long_to_bytes(jwk["e"]), "big")
        return rsa_backend.construct_public_key(n, e)


_clerk_jwk_client = _ClerkJWKClient()


def _verify_clerk_jwt(token: str) -> dict:
    """验证 Clerk JWT 的签名和 issuer，返回 payload。"""
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if kid is None:
        raise ValueError("missing kid")

    public_key = _clerk_jwk_client.get_public_key(kid)
    if public_key is None:
        raise ValueError("unknown kid")

    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=settings.CLERK_ISSUER,
        options={"verify_aud": False},
    )
    return payload


# --- 依赖项：通过 Clerk JWT 获取当前用户 ---


def get_clerk_current_user(
    db: Session = Depends(get_db),
    token: HTTPAuthorizationCredentials = Depends(security),
) -> "User":
    """
    验证 Clerk JWT 并返回对应用户。
    过渡期同时支持 clerk_id 匹配和 email 匹配，老用户无需迁移即可登录。
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的 Clerk 认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = _verify_clerk_jwt(token.credentials)
    except (JWTError, ValueError):
        raise credentials_exception

    clerk_sub: str = payload.get("sub")
    if not clerk_sub or not clerk_sub.startswith("user_"):
        raise credentials_exception

    # 优先用 clerk_id 匹配
    user = db.query(User).filter(User.clerk_id == clerk_sub).first()

    # 过渡期兜底：按 email 自动绑定（老用户无 clerk_id 时）
    if user is None:
        email = payload.get("email")
        if email:
            user = db.query(User).filter(User.user_email == email).first()
            if user:
                user.clerk_id = clerk_sub
                db.commit()

    if user is None:
        raise credentials_exception
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户账户已禁用"
        )

    return user
