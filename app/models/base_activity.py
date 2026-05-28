from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Numeric,
    Float,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class BaseActivity(Base):
    """
    统一运动活动汇总基础模型，对应表 `t_base_activity`。
    完美对齐最新 PostgreSQL 物理表结构。
    """

    __tablename__ = "t_base_activity"

    # ---- 索引与联合约束配置 ----
    __table_args__ = (
        # 联合唯一约束: 确保同一渠道下的原始活动ID唯一
        UniqueConstraint(
            "source_type", "activity_id", name="uq_base_act_source_origin"
        ),
        # 单列索引: 第三方连接ID
        Index("idx_base_activities_connect", "base_connect_id"),
    )

    # ---- 主键与关联外键 ----
    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="用户ID",
    )
    base_connect_id = Column(Integer, nullable=False, comment="关联渠道连接ID")

    # ---- 数据来源追踪 ----
    source_type = Column(
        String(20), nullable=False, comment="数据来源: garmin, coros, strava 等"
    )
    activity_id = Column(String(64), nullable=False, comment="源渠道中的原始活动唯一ID")

    # ---- 基础信息 ----
    activity_name = Column(String(255), nullable=True, comment="活动名称")
    sport_type_raw = Column(
        String(50), nullable=True, comment="第三方渠道的原始运动类型"
    )
    sport_mode_raw = Column(
        Integer, nullable=True, comment="第三方渠道的原始运动模式/枚举"
    )

    # ---- 时间与空间 ----
    start_time_gmt = Column(
        DateTime(timezone=True), nullable=True, comment="标准运动开始时间 (UTC时间)"
    )
    start_time_local = Column(
        DateTime(timezone=False), nullable=True, comment="本地运动开始时间 (无时区)"
    )
    end_time_gmt = Column(
        DateTime(timezone=True), nullable=True, comment="运动结束时间 (UTC时间)"
    )

    # ---- 核心运动数据 ----
    distance_meters = Column(Numeric(12, 2), nullable=True, comment="总距离（米）")
    duration_seconds = Column(
        Numeric(10, 2), nullable=True, comment="总耗时（秒，含暂停）"
    )
    moving_duration_seconds = Column(
        Numeric(10, 2), nullable=True, comment="净运动耗时（秒）"
    )
    calories = Column(Numeric(10, 2), nullable=True, comment="消耗热量（大卡）")

    # ---- 生理与运动指标 ----
    average_hr = Column(Integer, nullable=True, comment="平均心率")
    max_hr = Column(Integer, nullable=True, comment="最大心率")
    average_cadence = Column(Integer, nullable=True, comment="平均步频（步/分钟）")
    max_cadence = Column(Integer, nullable=True, comment="最大步频")
    average_speed = Column(Numeric(8, 3), nullable=True, comment="平均速度")
    max_speed = Column(Numeric(8, 3), nullable=True, comment="最大速度")

    # ---- 地理位置与设备 ----
    start_lat = Column(Float, nullable=True, comment="起点纬度")
    start_lon = Column(Float, nullable=True, comment="起点经度")
    location_name = Column(String(255), nullable=True, comment="位置名称描述")
    device_id = Column(String(100), nullable=True, comment="设备硬件唯一ID")
    elevation_gain = Column(Numeric(10, 2), nullable=True, comment="累计爬升（米）")
    elevation_loss = Column(Numeric(10, 2), nullable=True, comment="累计下降（米）")

    # ---- 系统时间字段 ----
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
