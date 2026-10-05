from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminFitnessAge(Base):
    """
    佳明体能年龄快照，对应表 `t_garmin_fitness_age`。

    一人一行，不记录历史。数据来自 `/metrics-service/metrics/maxmet/daily/{d}/{d}`，
    该接口返回数组，落库时取最新一条。
    """

    __tablename__ = "t_garmin_fitness_age"

    __table_args__ = (
        UniqueConstraint("user_id", name="uk_t_garmin_fitness_age_user"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id = Column(
        BigInteger, ForeignKey("t_users.id"), nullable=False, comment="用户ID"
    )
    connect_id = Column(
        BigInteger,
        ForeignKey("t_base_connect.id"),
        nullable=True,
        comment="数据来源的连接ID(仅溯源用)",
    )
    calendar_date = Column(Date, nullable=True, comment="数据日期")

    fitness_age = Column(Numeric(5, 1), nullable=True, comment="体能年龄(岁)")
    vo2_max_value = Column(Numeric(5, 2), nullable=True, comment="最大摄氧量 VO2max")
    max_met = Column(Numeric(5, 2), nullable=True, comment="最大代谢当量 MET")

    raw = Column(JSON, nullable=True, comment="佳明原始响应")
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
