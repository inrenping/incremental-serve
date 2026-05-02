from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class CorosConnect(Base):
    """
    Coros 连接授权信息实体，对应数据库表 `t_coros_connect`。

    Attributes:
        id: 自增主键
        user_id: 关联 t_users 表的外键
        coros_user_id: 高驰内部唯一标识
        coros_account: 高驰登录账号
        coros_password_encrypted: 加密后的高驰密码
        access_token: 高驰 API 访问令牌
        access_token_expires_at: Access Token 过期时间
        is_active: 授权是否有效
        last_synced_at: 最近一次同步时间
        total_count: 同步的活动记录总数
        region: 地区
        created_at: 创建时间
        updated_at: 更新时间
    """

    __tablename__ = "t_coros_connect"
    __table_args__ = (
        UniqueConstraint("user_id", name="t_user_coros_auth_user_id_key"),
        Index("idx_coros_auth_sync_status", "is_active", "last_synced_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    coros_user_id = Column(String(64), comment="高驰内部唯一标识 (登录接口返回的 userId)")
    coros_account = Column(String(255), comment="高驰登录账号 (手机号或邮箱)")
    coros_password_encrypted = Column(Text, comment="加密后的高驰密码，用于 Token 失效后静默刷新")
    access_token = Column(Text, comment="高驰 API 访问令牌")
    access_token_expires_at = Column(DateTime(timezone=True), comment="Access Token 绝对过期时间 (通常为 30 天)")
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="授权是否有效 (Token失效或手动解绑后置为 false)",
    )
    total_count = Column(Integer, default=0, comment="同步的活动记录总数")
    last_synced_at = Column(DateTime(timezone=True), comment="该用户最近一次成功同步运动数据的时间")
    region = Column(Integer, comment="地区") 
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="记录创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="记录更新时间",
    )

    user = relationship("User")