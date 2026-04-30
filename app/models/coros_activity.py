from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Numeric,
    BigInteger,
)
from sqlalchemy.orm import relationship

from app.db.session import Base

class CorosActivity(Base):
    """
    高驰 (Coros) 运动活动详细数据模型。
    """
    __tablename__ = "t_coros_activities"
    __table_args__ = (
        Index("idx_coros_activities_start_time", "start_time"),
        Index("idx_coros_activities_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    coros_connect_id = Column(
        Integer,
        ForeignKey("t_coros_connect.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_coros_connect 表的外键",
    )
    
    label_id = Column(String(64), unique=True, nullable=False, comment="高驰活动唯一标识 (labelId)")
    name = Column(String(255), comment="活动名称")
    sport_type = Column(Integer, comment="运动类型枚举值")
    mode = Column(Integer, comment="运动模式")
    
    distance = Column(Numeric(12, 2), comment="总距离 (米)")
    duration = Column(Integer, comment="总耗时 (秒)")
    calories = Column(Numeric(10, 2), comment="消耗热量 (大卡)")
    avg_hr = Column(Integer, comment="平均心率")
    max_hr = Column(Integer, comment="最大心率")
    
    start_time = Column(DateTime(timezone=True), comment="运动开始时间 (UTC)")
    end_time = Column(DateTime(timezone=True), comment="运动结束时间 (UTC)")
    
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
    coros_connect = relationship("CorosConnect")