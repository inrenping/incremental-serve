from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.db.session import Base

class User(Base):
    """
    用户模型类，对应数据库中的 `t_users` 表。

    该模型存储系统的核心用户信息，包括凭据、时间戳和激活状态。

    Attributes:
        user_id (int): 自增主键，唯一标识。
        user_name (str): 唯一用户名，用于登录和显示。
        user_email (str): 唯一电子邮件地址。
        created_at (datetime): 记录创建的时间戳（带时区）。
        updated_at (datetime): 记录最后一次更新的时间戳（带时区）。
        active (bool): 账户激活状态。默认为 False，需通过验证后激活。
    """
    __tablename__ = "t_users"

    user_id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, unique=True)
    user_email = Column(String, unique=True)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    active = Column(Boolean, default=False)