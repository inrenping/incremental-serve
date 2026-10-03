from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class SleepDaily(Base):
    """
    用户每日睡眠汇总数据模型，对应表 `t_sleep_daily`。

    口径说明：calendar_date 采用佳明口径，即**起床那天**
    （10/2 23:30 睡到 10/3 07:00 记为 10/3），与佳明 App 保持一致。
    """

    __tablename__ = "t_sleep_daily"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 同一用户每天只有一条睡眠汇总
        UniqueConstraint("user_id", "calendar_date", name="uk_t_sleep_daily_user_date"),
        # 单列索引: 用户ID
        Index("idx_t_sleep_daily_user_id", "user_id"),
        # 单列索引: 统计日期
        Index("idx_t_sleep_daily_calendar_date", "calendar_date"),
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
        comment="统计日期(佳明口径: 起床那天)",
    )
    sleep_start_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="入睡时刻(UTC)",
    )
    sleep_end_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="醒来时刻(UTC)",
    )
    local_offset_minutes = Column(
        Integer,
        nullable=True,
        comment="设备本地时区相对 UTC 的偏移分钟数",
    )
    sleep_time_seconds = Column(
        Integer,
        nullable=True,
        comment="睡眠总时长(秒, 不含小睡)",
    )
    nap_time_seconds = Column(
        Integer,
        nullable=True,
        comment="小睡时长(秒)",
    )
    deep_sleep_seconds = Column(
        Integer,
        nullable=True,
        comment="深睡时长(秒)",
    )
    light_sleep_seconds = Column(
        Integer,
        nullable=True,
        comment="浅睡时长(秒)",
    )
    rem_sleep_seconds = Column(
        Integer,
        nullable=True,
        comment="REM 时长(秒)",
    )
    awake_sleep_seconds = Column(
        Integer,
        nullable=True,
        comment="睡眠期间清醒时长(秒)",
    )
    unmeasurable_sleep_seconds = Column(
        Integer,
        nullable=True,
        comment="无法测量时长(秒)",
    )
    awake_count = Column(
        Integer,
        nullable=True,
        comment="清醒次数",
    )
    sleep_score = Column(
        Integer,
        nullable=True,
        comment="睡眠分数(0-100)",
    )
    average_sp_o2_value = Column(
        Float,
        nullable=True,
        comment="睡眠期间平均血氧(%)",
    )
    lowest_sp_o2_value = Column(
        Integer,
        nullable=True,
        comment="睡眠期间最低血氧(%)",
    )
    average_respiration_value = Column(
        Float,
        nullable=True,
        comment="睡眠期间平均呼吸(次/分)",
    )
    avg_sleep_stress = Column(
        Float,
        nullable=True,
        comment="睡眠期间平均压力值",
    )
    sleep_window_confirmed = Column(
        Boolean,
        nullable=True,
        comment="睡眠时间窗是否已确认",
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
    details = relationship(
        "SleepDetail",
        back_populates="daily",
        cascade="all, delete-orphan",
    )
