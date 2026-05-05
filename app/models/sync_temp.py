from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import relationship

from app.db.session import Base


class SyncTemp(Base):
    """
    数据同步临时表实体，对应数据库表 `t_sync_temp`。
    用于存放从各个平台拉取下来、等待进行模糊匹配和清洗的原始数据。

    Attributes:
        id: 自增主键
        user_id: 关联 t_users 表的外键
        batch_id: 批次ID（用于区分不同批次的同步任务）
        ga_id: A平台数据ID
        gac_id: B平台数据ID
        ca_id: C平台数据ID
        created_at: 记录创建时间
        updated_at: 记录更新时间
    """

    __tablename__ = "t_sync_temp"
    __table_args__ = (
        Index("idx_sync_temp_user_batch", "user_id", "batch_id"),
        Index("idx_sync_temp_platform_ids", "ga_id", "gac_id", "ca_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    batch_id = Column(Integer, nullable=True, comment="批次ID（用于区分不同批次的同步任务）")
    ga_id = Column(Integer, nullable=True, comment="佳明国际版")
    gac_id = Column(Integer, nullable=True, comment="佳明中国版")
    ca_id = Column(Integer, nullable=True, comment="高驰")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
        comment="记录创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
        comment="记录更新时间",
    )

    user = relationship("User")