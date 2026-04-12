from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import INET 
from app.db.session import Base

class UserVerifyCode(Base):
    __tablename__ = "t_user_verify_codes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(100), nullable=False, index=True) # 增加索引提高查询效率
    code = Column(String(10), nullable=False)    
    purpose = Column(String(20), nullable=False)    
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True))
    used = Column(Boolean, default=False)
    ip_address = Column(INET)