"""验证码服务"""

import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
import resend
from app.core.config import settings
from app.models.user_verify_code import UserVerifyCode
from app.models.user import User


def generate_captcha_code() -> str:
    """生成6位随机验证码"""
    return f"{random.randint(100000, 999999)}"


def verify_captcha_logic(
    db: Session, email: str, code: str, purpose: str
) -> UserVerifyCode:
    """
    验证验证码逻辑
    检查验证码是否存在、有效、未过期、未使用
    返回验证码记录并标记为已使用
    """
    now = datetime.now(timezone.utc)
    db_code = (
        db.query(UserVerifyCode)
        .filter(
            UserVerifyCode.email == email,
            UserVerifyCode.code == code,
            UserVerifyCode.purpose == purpose,
            UserVerifyCode.used == False,
            UserVerifyCode.expires_at > now,
        )
        .first()
    )

    if not db_code:
        raise HTTPException(status_code=400, detail="验证码无效、已过期或已使用")

    # 标记验证码已使用
    db_code.used = True
    return db_code


def send_captcha_email(email: str, code: str) -> Dict[str, Any]:
    """
    通过 Resend 发送验证码邮件
    """
    try:
        response = resend.Emails.send(
            {
                "from": settings.RESEND_EMAIL_FROM,
                "to": [email],
               "subject": "incremental.icu 一次性代码",
                "html": f"""
                <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 550px; margin: 0 auto; padding: 20px; color: #333333; line-height: 1.6;">
                    <div style="padding: 10px 0; border-bottom: 1px solid #eee; margin-bottom: 20px;">
                        <span style="font-size: 18px; font-weight: bold; color: #0078d4;">incremental.icu</span>
                    </div>
                    
                    <p style="font-size: 14px; margin-bottom: 20px;">
                        <strong>{email}</strong>，你好！
                    </p>
                    
                    <p style="font-size: 14px;">
                        我们已收到你要求获得 incremental.icu 所用的一次性代码的申请。
                    </p>
                    
                    <p style="font-size: 14px; margin-top: 25px; margin-bottom: 10px;">
                        你的一次性代码为: 
                        <span style="font-size: 24px; font-weight: bold; color: #2b2b2b; letter-spacing: 2px; margin-left: 5px;">
                            {code}
                        </span>
                    </p>
                    
                    <div style="background-color: #f8f9fa; border-left: 4px solid #ffb900; padding: 12px; margin: 25px 0; font-size: 13px; color: #666666;">
                        仅在网站(incremental.icu)上输入此代码。该代码在 5 分钟内有效。不要与任何人共享。我们绝不会在网站之外（如电话或邮件）索要它，打死也不要告诉别人！
                    </div>
                    
                    <p style="font-size: 14px; margin-top: 30px; line-height: 1.2;">
                        谢谢，<br>
                        <span style="color: #666666; font-weight: bold;">incremental.icu 团队</span>
                    </p>
                </div>
            """,
            }
        )
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"邮件发送失败: {str(e)}",
        )


def create_and_send_captcha(
    db: Session, email: str, purpose: str, ip_address: str
) -> Dict[str, Any]:
    """
    生成验证码、存入数据库、发送邮件

    Args:
        db: 数据库会话
        email: 目标邮箱
        purpose: 用途 ('register' 或 'login')
        ip_address: 请求IP地址

    Returns:
        包含 message 和 id 的响应字典
    """
    # 注册时检查邮箱是否已注册
    if purpose == "register":
        existing_email = db.query(User).filter(User.user_email == email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="该邮箱已注册")

    code = generate_captcha_code()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=5)

    # 构造数据库记录
    db_captcha = UserVerifyCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=expires_at,
        created_at=now,
        used=False,
        ip_address=ip_address,
    )

    # 发送邮件
    print(f"发送验证码到 {email}， {code}")
    response = send_captcha_email(email, code)

    # 邮件发送成功后，将记录写入数据库
    db.add(db_captcha)
    db.commit()

    return {"message": "验证码已发送", "id": response["id"]}
