from sqlalchemy import Column, Integer, String
from app.db.session import Base

class User(Base):
    __tablename__ = "t_users"

    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, unique=True)
    user_email = Column(String, unique=True)