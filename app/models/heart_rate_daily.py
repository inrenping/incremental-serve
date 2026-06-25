from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class HeartRateDaily(Base):
    """
    用户每日心率汇总数据模型，对应表 `t_heart_rate_daily`。
    存储用户每天的心率汇总指标，包括最大/最小/静息心率等。
    """

    __tablename__ = "t_heart_rate_daily"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 同一用户每天只有一条心率汇总
        UniqueConstraint(
            "user_id", "calendar_date", name="uk_t_heart_rate_daily_user_date"
        ),
        # 单列索引: 用户ID
        Index("idx_t_heart_rate_daily_user_id", "user_id"),
        # 单列索引: 统计日期
        Index("idx_t_heart_rate_daily_calendar_date", "calendar_date"),
    )

    # ---- 主键与关联外键 ----
    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )
    user_id = Column(
        BigInteger,
        ForeignKey("t_users.id"),
        nullable=False,
        comment="用户ID",
    )

    # ---- 核心数据 ----
    calendar_date = Column(
        Date,
        nullable=False,
        comment="统计日期",
    )
    max_heart_rate = Column(
        Integer,
        nullable=True,
        comment="当日最大心率(BPM)",
    )
    min_heart_rate = Column(
        Integer,
        nullable=True,
        comment="当日最小心率(BPM)",
    )
    resting_heart_rate = Column(
        Integer,
        nullable=True,
        comment="静息心率(BPM)",
    )
    last_seven_days_avg_resting_heart_rate = Column(
        Integer,
        nullable=True,
        comment="最近7天平均静息心率(BPM)",
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
    user = relationship("User")
