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

router = APIRouter(prefix="/auth", tags=["认证"])

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
    db.flush()  # 刷新以获取 new_user.user_id

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
    purpose: str,  # "login" 或 "register"
    request: Request, 
    db: Session = Depends(get_db)
):
    """
    生成验证码，存入数据库，并通过 Resend 发送邮件
    """
    # 1. 业务逻辑检查：如果是注册，检查邮箱是否已存在
    if purpose == "register":
        existing_email = db.query(User).filter(User.user_email == email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="该邮箱已注册")

    # 2. 生成 6 位随机验证码
    code = f"{random.randint(100000, 999999)}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)

    # 3. 构造数据库记录 (使用你的 UserVerifyCode 模型)
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
        # 4. 调用 Resend 发送邮件
        # 注意：如果你没验证域名，from 必须用 "onboarding@resend.dev"
        r = resend.Emails.send({
            "from": "Auth <onboarding@resend.dev>",
            "to": [email],
            "subject": f"您的验证码是: {code}",
            "html": f"""
                <p>您好，</p>
                <p>您的验证码为：<strong>{code}</strong></p>
                <p>有效期为 5 分钟。如果您没有请求此代码，请忽略此邮件。</p>
            """
        })
        
        # 5. 邮件发送成功后，将记录写入数据库
        db.add(db_captcha)
        db.commit()
        
        return {"message": "验证码已发送", "id": r["id"]}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"邮件发送失败: {str(e)}"
        )