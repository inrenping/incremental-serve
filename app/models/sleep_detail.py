from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base

# 佳明 sleepLevels.activityLevel 的阶段编码
SLEEP_LEVEL_DEEP = 0
SLEEP_LEVEL_LIGHT = 1
SLEEP_LEVEL_REM = 2
SLEEP_LEVEL_AWAKE = 3


class SleepDetail(Base):
    """
    用户睡眠阶段片段模型，对应表 `t_sleep_detail`。

    与心率明细不同，睡眠明细存的是**阶段片段**（开始/结束时间 + 阶段），
    而不是等距采样点。通过 daily_id 关联每日汇总表，天然按用户隔离。
    """

    __tablename__ = "t_sleep_detail"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 同一日同一起始时刻唯一
        UniqueConstraint("daily_id", "start_at", name="uk_t_sleep_detail_daily_start"),
        # 单列索引: 每日睡眠汇总ID
        Index("idx_t_sleep_detail_daily_id", "daily_id"),
        # 单列索引: 片段起始时间
        Index("idx_t_sleep_detail_start_at", "start_at"),
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
        comment="片段开始时刻(UTC)",
    )
    end_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="片段结束时刻(UTC)",
    )
    duration_seconds = Column(
        Integer,
        nullable=False,
        comment="片段时长(秒)",
    )
    activity_level = Column(
        Integer,
        nullable=False,
        comment="睡眠阶段: 0=深睡 1=浅睡 2=REM 3=清醒",
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
    daily = relationship("SleepDaily", back_populates="details")
