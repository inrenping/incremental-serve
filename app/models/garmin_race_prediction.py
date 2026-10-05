from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminRacePrediction(Base):
    """
    佳明比赛成绩预测，对应表 `t_garmin_race_prediction`。

    一人每个项目一行（5K / 10K / 半马 / 全马），不记录历史。
    数据来自 `/metrics-service/metrics/racepredictions`；
    注意 2026-10 实测该端点对无手表数据的账号返回 404，
    取不到时可用 PR 或 VO2max 自行按 Riegel 公式估算。
    """

    __tablename__ = "t_garmin_race_prediction"

    __table_args__ = (
        UniqueConstraint(
            "user_id", "race_type", name="uk_t_garmin_race_prediction_user_type"
        ),
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

    race_type = Column(
        String(24),
        nullable=False,
        comment="项目: FIVE_K/TEN_K/HALF_MARATHON/MARATHON",
    )
    distance_meters = Column(Numeric(10, 2), nullable=True, comment="项目距离(米)")
    predicted_seconds = Column(Numeric(12, 3), nullable=True, comment="预测完赛时间(秒)")
    predicted_time_text = Column(
        String(32), nullable=True, comment="接口直接给的文本成绩, 如 21:17"
    )

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
