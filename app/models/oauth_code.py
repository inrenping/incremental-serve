from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from app.db.session import Base


class OAuthAuthorizationCode(Base):
    __tablename__ = "t_oauth_authorization_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("t_users.id", ondelete="CASCADE"), nullable=False
    )
    code = Column(String(64), nullable=False, unique=True, index=True)
    client_id = Column(String(50), nullable=False)
    redirect_uri = Column(String(500), nullable=True)
    scope = Column(String(200), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
