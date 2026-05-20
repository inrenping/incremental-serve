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


class BaseConnect(Base):
    """
    统一第三方运动平台授权绑定基础模型，对应表 `t_base_connect`。
    整合了佳明（Garmin）、高驰（Coros）等各渠道的授权凭证与同步状态。
    """

    __tablename__ = "t_base_connect"
    __table_args__ = (
        Index("idx_base_source_id", "source_type", "source_connect_id"),
        Index("idx_base_user_source", "user_id", "source_type"),
        # 推荐增加的联合唯一约束：同一个用户在同一个运动平台上，只能拥有一条有效的绑定记录
        UniqueConstraint("user_id", "source_type", name="uq_user_base_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键 ID")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="本地系统用户 ID（关联 t_users 表）",
    )

    # ---- 渠道与基础业务字段 ----
    source_type = Column(
        String(20),
        nullable=False,
        comment="三方平台类型/来源标识（如：garmin, coros, strava）",
    )
    source_connect_id = Column(
        Integer,
        nullable=False,
        comment="用于业务区分的来源连接 ID / 状态控制 ID",
    )
    account = Column(String(255), nullable=True, comment="三方平台登录账号（如邮箱或手机号）")
    guid = Column(
        String(255),
        nullable=True,
        comment="三方平台内部的用户唯一标识（如 Garmin 的 GUID 或 Coros 的 UserID）",
    )
    password_encrypted = Column(
        Text,
        nullable=True,
        comment="加密存储的三方平台登录密码（用于自动刷新或后台重连）",
    )
    region = Column(
        String(50),
        nullable=True,
        comment="三方平台账号所属区域（如：CN、US，或数字编码）",
    )

    # ---- 状态与统计指标 ----
    is_active = Column(
        Boolean,
        default=True,
        nullable=True,
        comment="绑定是否激活/有效（对应建表语句的通用状态设计）",
    )
    total_count = Column(
        Integer,
        default=0,
        nullable=True,
        comment="该平台累计同步成功的运动数据总条数",
    )
    last_synced_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近一次从该三方平台同步数据的时间",
    )

    # ---- OAuth 2.0 核心凭证 ----
    access_token = Column(Text, nullable=True, comment="OAuth 2.0 访问令牌")
    access_token_expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="访问令牌（Access Token）的过期截止时间",
    )
    refresh_token = Column(Text, nullable=True, comment="OAuth 2.0 刷新令牌")
    refresh_token_expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="刷新令牌（Refresh Token）的过期截止时间",
    )

    # ---- OAuth 1.0 兼容与加固凭证 ----
    oauth_token = Column(
        String(255),
        nullable=True,
        comment="OAuth 1.0 访问令牌（针对老版本 Garmin 等接口兼容）",
    )
    oauth_token_secret = Column(
        String(255),
        nullable=True,
        comment="OAuth 1.0 访问令牌密钥（针对老版本 Garmin 等接口兼容）",
    )
    secret_string = Column(
        Text,
        nullable=True,
        comment="平台特有的加固/备用凭证字符串（如签名密钥、加盐字段等）",
    )

    # ---- 系统管理时间字段 ----
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

    # ---- 关系映射 ----
    user = relationship("User")