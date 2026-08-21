import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User

router = APIRouter()

# --- svix 签名验证 ---


def _verify_svix_signature(payload: bytes, headers: dict, secret: str) -> bool:
    """
    验证 Clerk 发出的 svix-signature。
    Clerk 使用 HMAC-SHA256 签名，格式：svix-timestamp:svix-signature。
    """
    sig_header = headers.get("svix-signature", "")
    timestamp_str = headers.get("svix-timestamp", "")

    if not sig_header or not timestamp_str:
        return False

    # 检查时间戳是否在 5 分钟内（防重放）
    try:
        timestamp = int(timestamp_str)
        if abs(time.time() - timestamp) > 300:
            return False
    except (ValueError, TypeError):
        return False

    # 构造签名内容
    signed_content = f"{timestamp_str}.{payload.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # 可能有多个签名，用空格分隔
    for sig in sig_header.split(" "):
        parts = sig.split(",", 1)
        if len(parts) == 2:
            _, sig_value = parts
            if hmac.compare_digest(f"v1,{expected}", sig_value):
                return True

    return False


# --- Webhook 端点 ---


@router.post("/clerk")
async def clerk_webhook(request: Request):
    """接收 Clerk Webhook 事件，同步用户数据。"""
    raw_body = await request.body()

    webhook_secret = settings.CLERK_WEBHOOK_SECRET
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # 验证签名
    if not _verify_svix_signature(raw_body, dict(request.headers), webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = json.loads(raw_body)
    event_type = event.get("type")
    data = event.get("data", {})

    db = SessionLocal()
    try:
        if event_type == "user.created":
            _handle_user_created(db, data)
        elif event_type == "user.updated":
            _handle_user_updated(db, data)
        elif event_type == "user.deleted":
            _handle_user_deleted(db, data)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {"status": "ok"}


# --- 事件处理函数 ---


def _handle_user_created(db, data: dict):
    """user.created: Clerk 新用户创建，按邮箱绑定到已有 t_users 记录，或创建新用户。"""
    clerk_id = data.get("id")
    if not clerk_id:
        return

    # 提取主邮箱
    email_addresses = data.get("email_addresses", [])
    primary_email_id = data.get("primary_email_address_id")
    email = None
    for addr in email_addresses:
        if addr.get("id") == primary_email_id:
            email = addr.get("email_address")
            break
    if not email and email_addresses:
        email = email_addresses[0].get("email_address")

    if not email:
        return

    # 提取用户名（first_name + last_name）
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip()
    if not full_name:
        full_name = email.split("@")[0]  # 使用邮箱前缀作为用户名

    # 按 email 查找已有用户（大小写不敏感）
    user = (
        db.query(User).filter(func.lower(User.user_email) == func.lower(email)).first()
    )
    if user:
        # 已有用户，绑定 clerk_id
        user.clerk_id = clerk_id
        if not user.user_name:
            user.user_name = full_name
        db.commit()
    else:
        # 新用户，创建记录
        now = datetime.now(timezone.utc)
        new_user = User(
            clerk_id=clerk_id,
            user_email=email,
            user_name=full_name,
            active=True,  # Clerk 已验证，直接激活
            created_at=now,
            updated_at=now,
        )
        db.add(new_user)
        db.commit()


def _handle_user_updated(db, data: dict):
    """user.updated: 同步 Clerk 侧的 email 和 name 变更，或创建新用户。"""
    clerk_id = data.get("id")
    if not clerk_id:
        return

    user = db.query(User).filter(User.clerk_id == clerk_id).first()
    if not user:
        # 还没绑定，尝试按 email 绑定（大小写不敏感）
        email_addresses = data.get("email_addresses", [])
        primary_email_id = data.get("primary_email_address_id")
        email = None
        for addr in email_addresses:
            if addr.get("id") == primary_email_id:
                email = addr.get("email_address")
                break
        if email:
            user = (
                db.query(User)
                .filter(func.lower(User.user_email) == func.lower(email))
                .first()
            )
            if user:
                user.clerk_id = clerk_id

    # 如果还是找不到用户，创建新用户
    if not user:
        email_addresses = data.get("email_addresses", [])
        primary_email_id = data.get("primary_email_address_id")
        email = None
        for addr in email_addresses:
            if addr.get("id") == primary_email_id:
                email = addr.get("email_address")
                break
        if not email and email_addresses:
            email = email_addresses[0].get("email_address")

        if not email:
            return

        # 提取用户名
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = email.split("@")[0]

        # 检查用户名是否已存在
        base_name = full_name
        counter = 1
        while db.query(User).filter(User.user_name == full_name).first():
            full_name = f"{base_name}_{counter}"
            counter += 1

        now = datetime.now(timezone.utc)
        user = User(
            clerk_id=clerk_id,
            user_email=email,
            user_name=full_name,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.commit()
        return

    # 同步 email（如果变更）
    email_addresses = data.get("email_addresses", [])
    primary_email_id = data.get("primary_email_address_id")
    for addr in email_addresses:
        if addr.get("id") == primary_email_id:
            new_email = addr.get("email_address")
            if new_email and new_email != user.user_email:
                user.user_email = new_email
            break

    # 同步 name（如果变更）
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip()
    if full_name and full_name != user.user_name:
        user.user_name = full_name

    db.commit()


def _handle_user_deleted(db, data: dict):
    """user.deleted: Clerk 用户删除，清除 clerk_id 映射。"""
    clerk_id = data.get("id")
    if not clerk_id:
        return

    user = db.query(User).filter(User.clerk_id == clerk_id).first()
    if user:
        user.clerk_id = None
        db.commit()
