import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
from jose import jwt

# --- 配置建议放在 app/core/config.py 中 ---
SECRET_KEY = "你的超级加密密钥_务必换成复杂的随机字符串" 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # Access Token 有效期 30 分钟

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    生成 JWT Access Token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # 添加过期时间戳 (exp)
    to_encode.update({"exp": expire})
    
    # 编码生成 JWT
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token() -> str:
    """
    生成一个高熵的随机字符串作为 Refresh Token
    因为 Refresh Token 存储在数据库中，使用随机长字符串比 JWT 更灵活
    """
    return secrets.token_urlsafe(64)

def decode_access_token(token: str) -> Optional[dict]:
    """
    解析并验证 Access Token
    """
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return decoded_token if decoded_token["exp"] >= datetime.now(timezone.utc).timestamp() else None
    except Exception:
        return None