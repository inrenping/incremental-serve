from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class SleepNap(Base):
    """
    用户小睡明细模型，对应表 `t_sleep_nap`。

    与睡眠阶段片段（t_sleep_detail）同构：通过 daily_id 关联每日汇总表，
    自身不带 user_id，天然按用户隔离。数据来源是佳明 dailySleepDTO.dailyNapDTOS，
    每条小睡有独立的起止时间（UTC）。
    """

    __tablename__ = "t_sleep_nap"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 同一日同一起始时刻唯一
        UniqueConstraint("daily_id", "start_at", name="uk_t_sleep_nap_daily_start"),
        # 单列索引: 每日睡眠汇总ID
        Index("idx_t_sleep_nap_daily_id", "daily_id"),
        # 单列索引: 小睡起始时间
        Index("idx_t_sleep_nap_start_at", "start_at"),
    )

    # ---- 主键与关联外键 ----
    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )
    daily_id = Column(
        BigInteger,
        ForeignKey("t_sleep_daily.id", ondelete="CASCADE"),
        nullable=False,
        comment="每日睡眠汇总ID",
    )

    # ---- 核心数据 ----
    start_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="小睡开始时刻(UTC)",
    )
    end_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="小睡结束时刻(UTC)",
    )
    duration_seconds = Column(
        Integer,
        nullable=False,
        comment="小睡时长(秒)",
    )
    nap_feedback = Column(
        String(128),
        nullable=True,
        comment="佳明小睡反馈枚举，如 LATE_TIMING_LONG_DURATION_LOW_NEED",
    )

    # ---- 系统时间字段 ----
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    # ---- 关系映射 ----
    daily = relationship("SleepDaily", back_populates="naps")
