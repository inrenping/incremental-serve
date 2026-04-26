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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminConnect(Base):
    """
    Garmin 连接授权信息实体，对应数据库表 `t_garmin_connect`。

    Attributes:
        id: 自增主键
        user_id: 关联 t_users 表的外键
        region: 账号所属区域
        garmin_guid: 佳明内部唯一标识
        oauth_token: OAuth 1.0 访问令牌
        oauth_token_secret: OAuth 1.0 令牌密钥
        access_token: OAuth 2.0 Access Token
        refresh_token: OAuth 2.0 Refresh Token
        access_token_expires_at: Access Token 过期时间
        refresh_token_expires_at: Refresh Token 过期时间
        is_active: 授权是否有效
        last_synced_at: 最近一次同步时间
        created_at: 创建时间
        updated_at: 更新时间
    """

    __tablename__ = "t_garmin_connect"
    __table_args__ = (
        UniqueConstraint("user_id", "region", name="t_user_garmin_auth_user_id_region_key"),
        Index("idx_garmin_auth_region", "region"),
        Index("idx_garmin_auth_sync_status", "is_active", "last_synced_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    region = Column(
        String(10),
        nullable=False,
        default="CN",
        comment="账号所属区域: CN(中国版), GLOBAL(国际版)",
    )
    garmin_guid = Column(UUID(as_uuid=True), comment="佳明内部唯一标识 (通过 JWT 解析)")
    garmin_display_name = Column(String(255), comment="佳明显示名称")
    oauth_token = Column(String(255), comment="OAuth 1.0 访问令牌")
    oauth_token_secret = Column(String(255), comment="OAuth 1.0 令牌密钥")
    access_token = Column(Text, comment="OAuth 2.0 Access Token (JWT)")
    refresh_token = Column(Text, comment="OAuth 2.0 Refresh Token (用于刷新 Access Token，有效期约30天)")
    access_token_expires_at = Column(DateTime(timezone=True), comment="Access Token 绝对过期时间")
    refresh_token_expires_at = Column(DateTime(timezone=True), comment="Refresh Token 绝对过期时间")
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="授权是否有效 (Token失效或手动解绑后置为 false)",
    )
    last_synced_at = Column(DateTime(timezone=True), comment="该用户最近一次成功同步运动数据的时间")
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
