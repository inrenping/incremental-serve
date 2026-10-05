from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class GarminPersonalRecord(Base):
    """
    佳明个人纪录 PR，对应表 `t_garmin_personal_record`。

    一人每个项目一行，不记录历史：PR 破了就覆盖同一行。
    数据来自 `/personalrecord-service/personalrecord/prs/{displayName}`，
    注意路径里必须带 displayName（socialProfile 里的那个 UUID），带 userProfileId 会 403。

    type_id 对跑步的含义：1=1km 2=1mile 3=5km 4=10km 5=半马 6=全马 7=最长跑。
    其中 1~6 的 value 单位是秒，7 是米，落库时按 unit 拆到 value_seconds / value_meters。
    """

    __tablename__ = "t_garmin_personal_record"

    __table_args__ = (
        # activity_type 可能为 NULL，唯一键里用 COALESCE 兜底，避免 NULL 互不相等
        Index(
            "uk_t_garmin_personal_record_user_type",
            "user_id",
            "type_id",
            text("COALESCE(activity_type, '')"),
            unique=True,
        ),
        Index("idx_t_garmin_personal_record_activity", "activity_type"),
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

    type_id = Column(Integer, nullable=False, comment="佳明 PR 类型ID(跑步: 1=1km ... 7=最长跑)")
    type_key = Column(
        String(32), nullable=True, comment="本地语义键, 如 fastest_5k / longest_run"
    )
    activity_type = Column(
        String(32), nullable=True, comment="运动类型: running/cycling/road_biking"
    )
    unit = Column(String(16), nullable=True, comment="value 单位: second/meter")
    value = Column(Numeric(14, 3), nullable=True, comment="纪录原始值(按 unit 解释)")
    value_seconds = Column(Numeric(12, 3), nullable=True, comment="用时类纪录(秒)")
    value_meters = Column(Numeric(14, 3), nullable=True, comment="距离类纪录(米)")

    activity_id = Column(BigInteger, nullable=True, comment="创纪录的那次活动ID")
    activity_name = Column(String(255), nullable=True, comment="创纪录的活动名称")
    achieved_at = Column(
        DateTime(timezone=True), nullable=True, comment="创纪录时刻(UTC, prStartTimeGmt)"
    )
    activity_start_at = Column(
        DateTime(timezone=True), nullable=True, comment="活动开始时刻(UTC)"
    )

    raw = Column(JSON, nullable=True, comment="佳明原始条目")
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
