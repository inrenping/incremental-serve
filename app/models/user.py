from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.db.session import Base

class User(Base):
    __tablename__ = "t_users"

    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, unique=True)
    user_email = Column(String, unique=True)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    active = Column(Boolean, default=False) 