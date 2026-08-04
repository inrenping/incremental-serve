from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from app.db.session import Base


class OAuthVerifyCode(Base):
    """
    用户 OAuth 授权验证码（一次性短码）。

    用户登录主站后，在 /dash/gpt 页面查看此验证码，
    在 OpenAI / ChatGPT 的授权页直接输入即可完成 OAuth 授权。
    """

    __tablename__ = "t_user_oauth_verify_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("t_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(String(10), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
