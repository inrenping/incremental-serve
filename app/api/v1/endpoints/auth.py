from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_
import random
import resend

from app.db.session import get_db
from app.models.user import User
from app.models.user_verify_code import UserVerifyCode
from app.models.refresh_token import UserRefreshToken
from app.core.security import create_access_token, create_refresh_token

router = APIRouter()

# --- 辅助方法：统一验证码校验逻辑 ---
def verify_captcha_logic(db: Session, email: str, code: str, purpose: str):
    now = datetime.now(timezone.utc)
    db_code = db.query(UserVerifyCode).filter(
        UserVerifyCode.email == email,
        UserVerifyCode.code == code,
        UserVerifyCode.purpose == purpose,
        UserVerifyCode.used == False,
        UserVerifyCode.expires_at > now
    ).first()
    
    if not db_code:
        raise HTTPException(status_code=400, detail="验证码无效、已过期或已使用")
    
    # 标记验证码已使用
    db_code.used = True
    return db_code

# --- 1. 注册接口 ---
@router.post("/register")
def register(
    username: str, 
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    # A. 校验用户名和邮箱是否已存在
    existing_user = db.query(User).filter(
        or_(User.user_name == username, User.user_email == email)
    ).first()
    
    if existing_user:
        field = "用户名" if existing_user.user_name == username else "邮箱"
        raise HTTPException(status_code=400, detail=f"该{field}已被注册")

    # B. 校验验证码（purpose设为 'register'）
    verify_captcha_logic(db, email, captcha, "register")

    # C. 创建并激活用户
    new_user = User(
        user_name=username,
        user_email=email,
        active=True,
        created_at=datetime.now(timezone.utc)
    )
    db.add(new_user)
    db.flush()

    # D. 生成 Tokens
    tokens = generate_user_tokens(db, new_user, request)
    
    db.commit()
    return tokens

# --- 2. 登录接口 ---
@router.post("/login")
def login(
    email: str, 
    captcha: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    # A. 校验验证码（purpose设为 'login'）
    verify_captcha_logic(db, email, captcha, "login")

    # B. 查找用户
    user = db.query(User).filter(User.user_email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱未注册，请先注册")
    
    if not user.active:
        raise HTTPException(status_code=400, detail="账号已被禁用")

    # C. 生成 Tokens
    tokens = generate_user_tokens(db, user, request)
    
    db.commit()
    return tokens

# --- 3. 封装 Token 生成逻辑 ---
def generate_user_tokens(db: Session, user: User, request: Request):
    # 生成 Access Token (通常 15-60 分钟)
    access_token = create_access_token(data={"sub": str(user.user_id)})
    
    # 生成 Refresh Token (通常 7 天)
    refresh_token_str = create_refresh_token()
    
    new_refresh_token = UserRefreshToken(
        user_id=user.user_id,
        refresh_token=refresh_token_str,
        expires_time=datetime.now(timezone.utc) + timedelta(days=7),
        created_at=datetime.now(timezone.utc),
        expires_ip=request.client.host,
        user_agent=request.headers.get("user-agent"),
        revoked=False
    )
    db.add(new_refresh_token)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "user_id": user.user_id
    }

@router.post("/send-captcha")
def send_captcha(
    email: str, 
    purpose: str,
    request: Request, 
    db: Session = Depends(get_db)
):
    """
    生成验证码，存入数据库，并通过 Resend 发送邮件
    """
    if purpose == "register":
        existing_email = db.query(User).filter(User.user_email == email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="该邮箱已注册")

    code = f"{random.randint(100000, 999999)}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)

    # 构造数据库记录 (使用你的 UserVerifyCode 模型)
    db_captcha = UserVerifyCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=expires_at,
        created_at=now,
        used=False,
        ip_address=request.client.host
    )
    
    try:
        # 调用 Resend 发送邮件
        r = resend.Emails.send({
            "from": "Incremental <onboarding@resend.dev>",
            "to": [email],
            "subject": f"您的验证码是: {code}",
            "html": f"""
                <p>您好，</p>
                <p>您的验证码为：<strong>{code}</strong></p>
                <p>有效期为 5 分钟。如果您没有请求此代码，请忽略此邮件。</p>
            """
        })
        
        # 邮件发送成功后，将记录写入数据库
        db.add(db_captcha)
        db.commit()
        
        return {"message": "验证码已发送", "id": r["id"]}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"邮件发送失败: {str(e)}"
        )
@router.post("/refresh")
def refresh_token_endpoint(
    refresh_token: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    使用 Refresh Token 获取新的 Access Token。
    """
    now = datetime.now(timezone.utc)
    
    # 在数据库中查找该 Refresh Token
    db_token = db.query(UserRefreshToken).filter(
        UserRefreshToken.refresh_token == refresh_token,
        UserRefreshToken.revoked == False,
        UserRefreshToken.expires_time > now
    ).first()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Refresh Token 无效或已过期"
        )

    # 检查关联用户是否存在且活跃
    user = db.query(User).filter(User.user_id == db_token.user_id).first()
    if not user or not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="用户不存在或已被禁用"
        )

    # 标记旧的 Refresh Token 为已撤回（Rotation 机制）
    db_token.revoked = True
    
    # 生成一套全新的 Tokens
    new_tokens = generate_user_tokens(db, user, request)
    
    db.commit()
    return new_tokens