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
    BigInteger,
    SmallInteger,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminActivity(Base):
    """
    用户佳明运动活动详细数据模型，对应表 `t_garmin_activities`。
    """

    __tablename__ = "t_garmin_activities"
    __table_args__ = (
        Index("idx_activities_start_time", "start_time_gmt"),
        Index("idx_activities_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    garmin_connect_id = Column(
        Integer,
        ForeignKey("t_garmin_connect.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_garmin_connect 表的外键",
    )
    activity_id = Column(BigInteger, unique=True, nullable=False, comment="佳明活动唯一ID")
    activity_name = Column(String(255), comment="活动名称（如：宁波市 跑步）")
    activity_type_key = Column(String(50), comment="活动类型标识（如：running）")
    start_time_local = Column(DateTime, comment="当地开始时间")
    start_time_gmt = Column(DateTime(timezone=True), comment="格林威治标准时间")
    distance_meters = Column(Numeric(12, 2), comment="总距离（单位：米）")
    duration_seconds = Column(Numeric(10, 2), comment="总耗时（秒，含暂停）")
    moving_duration_seconds = Column(Numeric(10, 2), comment="净运动耗时（秒）")
    calories = Column(Numeric(10, 2), comment="消耗热量（大卡）")
    average_hr = Column(SmallInteger, comment="平均心率")
    max_hr = Column(SmallInteger, comment="最大心率")
    average_cadence = Column(SmallInteger, comment="平均步频（步/分钟）")
    max_cadence = Column(SmallInteger, comment="最大步频")
    average_speed = Column(Numeric(8, 3), comment="平均速度（米/秒）")
    max_speed = Column(Numeric(8, 3), comment="最大速度（米/秒）")
    start_lat = Column(Float, comment="起点纬度")
    start_lon = Column(Float, comment="起点经度")
    location_name = Column(String(255), comment="位置名称")
    device_id = Column(String(100), comment="佳明设备ID")
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

    user = relationship("User")
    garmin_connect = relationship("GarminConnect")
