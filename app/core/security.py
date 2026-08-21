import base64
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional, TYPE_CHECKING
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy import func
from sqlalchemy.orm import Session
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

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

    # 优先验证 Clerk JWT (RS256)；token 无效时回退旧版 HS256（过渡期兼容）
    try:
        user = _resolve_user_by_clerk(db, token.credentials, credentials_exception)
        if not user.active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="用户账户已禁用"
            )
        return user
    except (JWTError, ValueError):
        pass

    try:
        # 旧版 token：使用统一配置的 SECRET_KEY
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


def _b64url_to_int(value: str) -> int:
    """将 JWK 中的 base64url 编码值转换为 int。"""
    padding = "=" * (-len(value) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(value + padding), "big")


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
        # JWK base64url 参数 -> cryptography RSA public key
        n = _b64url_to_int(jwk["n"])
        e = _b64url_to_int(jwk["e"])
        return rsa.RSAPublicNumbers(e, n).public_key()


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


def _get_clerk_user_info(user_id: str) -> dict:
    """使用 Clerk Backend API 获取用户信息。"""
    if not settings.CLERK_SECRET_KEY:
        return {}

    url = f"https://api.clerk.com/v1/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {settings.CLERK_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _resolve_user_by_clerk(
    db: Session,
    credentials: str,
    credentials_exception: HTTPException,
) -> "User":
    """验证 Clerk JWT 并通过 clerk_id / email 解析用户。

    - token 无效（签名/格式/issuer 错误）时抛出 JWTError/ValueError，由调用方决定是否回退旧版。
    - token 有效但用户不存在时自动创建用户记录。
    """
    from datetime import datetime, timezone

    payload = _verify_clerk_jwt(credentials)

    clerk_sub: str = payload.get("sub")
    if not clerk_sub or not clerk_sub.startswith("user_"):
        raise ValueError("invalid clerk sub")

    # 优先用 clerk_id 匹配
    user = db.query(User).filter(User.clerk_id == clerk_sub).first()

    # 如果没有找到用户，通过 Clerk Backend API 获取用户信息
    email = None
    first_name = ""
    last_name = ""

    if user is None:
        clerk_user = _get_clerk_user_info(clerk_sub)
        if clerk_user:
            # 获取主邮箱
            email_addresses = clerk_user.get("email_addresses", [])
            primary_email_id = clerk_user.get("primary_email_id")
            for addr in email_addresses:
                if addr.get("id") == primary_email_id:
                    email = addr.get("email_address")
                    break
            if not email and email_addresses:
                email = email_addresses[0].get("email_address")

            # 获取用户名
            first_name = clerk_user.get("first_name", "") or ""
            last_name = clerk_user.get("last_name", "") or ""

    # 过渡期兜底：按 email 自动绑定（老用户无 clerk_id 时）
    # 使用大小写不敏感匹配，因为邮箱地址不区分大小写
    if user is None and email:
        user = (
            db.query(User)
            .filter(func.lower(User.user_email) == func.lower(email))
            .first()
        )
        if user:
            user.clerk_id = clerk_sub
            db.commit()

    # 如果还是找不到用户，自动创建新用户
    if user is None:
        if not email:
            raise credentials_exception

        # 提取用户名
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = email.split("@")[0]

        # 检查用户名是否已存在，如果存在则添加后缀
        base_name = full_name
        counter = 1
        while db.query(User).filter(User.user_name == full_name).first():
            full_name = f"{base_name}_{counter}"
            counter += 1

        # 创建新用户
        now = datetime.now(timezone.utc)
        user = User(
            clerk_id=clerk_sub,
            user_email=email,
            user_name=full_name,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


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
        user = _resolve_user_by_clerk(db, token.credentials, credentials_exception)
    except (JWTError, ValueError):
        raise credentials_exception
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户账户已禁用"
        )
    return user
