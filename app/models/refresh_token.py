from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.db.session import Base

class UserRefreshToken(Base):
    __tablename__ = "t_user_refresh_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("t_users.user_id"), nullable=False)    
    refresh_token = Column(Text, nullable=False)
    expires_time = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True))    
    expires_ip = Column(String(45))
    user_agent = Column(Text)    
    revoked = Column(Boolean, nullable=False, default=False)
    user = relationship("User", back_populates="refresh_tokens")