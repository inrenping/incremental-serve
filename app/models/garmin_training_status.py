from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminTrainingStatus(Base):
    """
    佳明训练状态与训练负荷快照，对应表 `t_garmin_training_status`。

    一人一行，不记录历史：每次同步直接覆盖。
    数据来自 `/metrics-service/metrics/trainingstatus/aggregated/{date}`，
    其中 mostRecentTrainingStatus 提供负荷与状态，mostRecentVO2Max 提供最大摄氧量。
    """

    __tablename__ = "t_garmin_training_status"

    __table_args__ = (
        UniqueConstraint("user_id", name="uk_t_garmin_training_status_user"),
        Index("idx_t_garmin_training_status_date", "calendar_date"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id = Column(
        BigInteger, ForeignKey("t_users.id"), nullable=False, comment="用户ID"
    )
    connect_id = Column(
        BigInteger,
        ForeignKey("t_base_connect.id"),
        nullable=True,
        comment="数据来源的连接ID(仅溯源用, 一人多连接时以最后一次同步为准)",
    )
    calendar_date = Column(Date, nullable=True, comment="数据日期(佳明口径)")

    # ---- 训练状态 ----
    training_status = Column(
        String(32),
        nullable=True,
        comment="训练状态: PRODUCTIVE/MAINTAINING/PEAKING/OVERREACHING/DETRAINING/UNPRODUCTIVE",
    )
    training_status_feedback_phrase = Column(
        Text, nullable=True, comment="佳明给的状态描述文案"
    )
    training_paused = Column(Boolean, nullable=True, comment="训练是否处于暂停状态")
    since_date = Column(Date, nullable=True, comment="当前状态起始日期")

    # ---- 训练负荷 ----
    weekly_training_load = Column(Numeric(10, 2), nullable=True, comment="7日训练负荷")
    daily_training_load_acute = Column(
        Numeric(10, 2), nullable=True, comment="急性负荷(短期)"
    )
    daily_training_load_chronic = Column(
        Numeric(10, 2), nullable=True, comment="慢性负荷(长期)"
    )
    acute_chronic_workload_ratio = Column(
        Numeric(6, 3), nullable=True, comment="ACWR 急慢性负荷比, >1.5 为危险区"
    )
    acwr_status = Column(String(32), nullable=True, comment="ACWR 状态: LOW/OPTIMAL/HIGH")
    acwr_percent = Column(Numeric(6, 2), nullable=True, comment="ACWR 百分比")
    load_tunnel_min = Column(
        Numeric(10, 2), nullable=True, comment="当前建议负荷区间下限"
    )
    load_tunnel_max = Column(
        Numeric(10, 2), nullable=True, comment="当前建议负荷区间上限"
    )
    load_level_trend = Column(String(32), nullable=True, comment="负荷水平趋势")
    fitness_trend = Column(String(32), nullable=True, comment="体能趋势")

    # ---- 最大摄氧量 ----
    vo2_max_value = Column(Numeric(5, 2), nullable=True, comment="最大摄氧量 VO2max")
    vo2_max_precise_value = Column(
        Numeric(6, 3), nullable=True, comment="VO2max 精确值"
    )
    vo2_max_calendar_date = Column(Date, nullable=True, comment="VO2max 测定日期")
    vo2_max_running = Column(Numeric(5, 2), nullable=True, comment="跑步 VO2max")
    vo2_max_cycling = Column(Numeric(5, 2), nullable=True, comment="骑行 VO2max")

    # ---- 原始响应与时间戳 ----
    raw = Column(JSON, nullable=True, comment="佳明原始响应(接口字段常变, 用它兜底)")
    synced_at = Column(DateTime(timezone=True), nullable=True, comment="最近同步时间")
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

    user = relationship("User")
