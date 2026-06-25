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


class HeartRateDetail(Base):
    """
    用户心率采样明细数据模型，对应表 `t_heart_rate_detail`。
    存储用户每天的心率采样点数据，与每日汇总表 `t_heart_rate_daily` 关联。
    """

    __tablename__ = "t_heart_rate_detail"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 同一日同一采样时间唯一
        UniqueConstraint(
            "daily_id", "sample_time", name="uk_t_heart_rate_detail_daily_time"
        ),
        # 单列索引: 每日心率汇总ID
        Index("idx_t_heart_rate_detail_daily_id", "daily_id"),
        # 单列索引: 采样时间
        Index("idx_t_heart_rate_detail_sample_time", "sample_time"),
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
        ForeignKey("t_heart_rate_daily.id", ondelete="CASCADE"),
        nullable=False,
        comment="每日心率汇总ID",
    )

    # ---- 核心数据 ----
    sample_time = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="心率采样时间",
    )
    heart_rate = Column(
        Integer,
        nullable=False,
        comment="心率值(BPM)",
    )

    # ---- 系统时间字段 ----
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # ---- 关系映射 ----
    daily = relationship("HeartRateDaily")
