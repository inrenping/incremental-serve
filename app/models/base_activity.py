from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Numeric,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class BaseActivity(Base):
    """
    统一运动活动汇总基础模型，对应表 `t_base_activity`。
    整合了佳明（Garmin）、高驰（Coros）等各渠道的运动数据。
    """

    __tablename__ = "t_base_activity"
    __table_args__ = (
        Index("idx_base_activity_user_start_time", "user_id", "start_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的用户ID",
    )

    # ---- 数据来源追踪 ----
    source_provider = Column(
        String(20),
        nullable=False,
        comment="数据来源: garmin(佳明), coros(高驰), strava 等",
    )
    activity_id = Column(
        String(64),
        nullable=False,
        unique=True,  # 配合 source_provider 构成唯一逻辑，若在DB层是联合唯一，可保持该字段的独立或通过联合约束表达
        comment="源渠道中的原始活动唯一ID（统一转为字符串存储）",
    )

    # ---- 基础信息 ----
    activity_name = Column(String(255), comment="活动名称（如：宁波市 跑步）")
    sport_type_standard = Column(
        String(50), comment="归一化后的标准运动类型（如：running, cycling, walking）"
    )
    sport_type_raw = Column(String(50), comment="第三方渠道的原始运动类型/枚举值")

    # ---- 时间与空间 ----
    start_time = Column(DateTime(timezone=True), comment="标准运动开始时间 (UTC时间)")
    end_time = Column(DateTime(timezone=True), comment="运动结束时间 (UTC时间)")
    duration_seconds = Column(Numeric(10, 2), comment="总耗时（秒，含暂停）")
    moving_duration_seconds = Column(Numeric(10, 2), comment="净运动耗时（秒）")

    # ---- 核心运动数据（统一单位） ----
    distance_meters = Column(Numeric(12, 2), comment="总距离（单位：米）")
    calories = Column(Numeric(10, 2), comment="消耗热量（大卡）")
    elevation_gain = Column(Numeric(10, 2), comment="累计爬升（米）")
    elevation_loss = Column(Numeric(10, 2), comment="累计下降（米）")

    # ---- 生理与运动指标 ----
    average_hr = Column(Integer, comment="平均心率")
    max_hr = Column(Integer, comment="最大心率")
    average_cadence = Column(Integer, comment="平均步频（步/分钟）")

    # ---- 后续新增的特定渠道冗余 ID (允许为空) ----
    coros_activity_id = Column(String(64), nullable=True, comment="高驰原始活动ID (冗余存储)")
    garmin_activity_id = Column(String(64), nullable=True, comment="佳明国际区原始活动ID (冗余存储)")
    garmin_cn_activity_id = Column(String(64), nullable=True, comment="佳明大陆区原始活动ID (冗余存储)")

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