from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class SyncRun(Base):
    """同步批次记录：每一次一键同步的执行汇总（唯一的批次级落库点）。"""

    __tablename__ = "t_sync_run"
    __table_args__ = (
        Index("idx_t_sync_run_user_created", "user_id", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="批次主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.id"),
        nullable=False,
        comment="用户 ID（关联 t_users 表）",
    )
    source_connect_id = Column(
        Integer,
        ForeignKey("t_base_connect.id"),
        nullable=False,
        comment="源账号连接配置 ID（关联 t_base_connect 表）",
    )
    target_connect_id = Column(
        Integer,
        ForeignKey("t_base_connect.id"),
        nullable=False,
        comment="目标账号连接配置 ID（关联 t_base_connect 表）",
    )
    window_size = Column(
        Integer, nullable=False, default=10, comment="每侧拉取的运动条数"
    )
    source_platform = Column(
        String(32), nullable=True, comment="源平台标识（garmin/garmin_cn/coros）"
    )
    target_platform = Column(
        String(32), nullable=True, comment="目标平台标识（garmin/garmin_cn/coros）"
    )
    source_account = Column(
        String(255), nullable=True, comment="源账号（冗余，便于展示 A→B）"
    )
    target_account = Column(
        String(255), nullable=True, comment="目标账号（冗余，便于展示 A→B）"
    )
    fetched_source = Column(Integer, nullable=False, default=0, comment="源端拉取条数")
    fetched_target = Column(Integer, nullable=False, default=0, comment="目标端拉取条数")
    diff_count = Column(Integer, nullable=False, default=0, comment="差异（待同步）条数")
    uploaded_count = Column(Integer, nullable=False, default=0, comment="成功同步条数")
    skipped_count = Column(Integer, nullable=False, default=0, comment="目标已存在（跳过）条数")
    failed_count = Column(Integer, nullable=False, default=0, comment="失败条数")
    status = Column(
        String(32),
        nullable=False,
        default="error",
        comment="批次状态：success/partial/failed/no_diff/error",
    )
    error_message = Column(Text, nullable=True, comment="批次级异常信息")
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="批次开始时间",
    )
    finished_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="批次结束时间",
    )
    duration_ms = Column(Integer, nullable=True, comment="耗时（毫秒）")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="记录创建时间",
    )

    items = relationship(
        "SyncRunItem",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class SyncRunItem(Base):
    """同步明细记录：批次中每一条运动的同步结果。"""

    __tablename__ = "t_sync_run_item"
    __table_args__ = (Index("idx_t_sync_run_item_run", "run_id"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="明细主键")
    run_id = Column(
        BigInteger,
        ForeignKey("t_sync_run.id"),
        nullable=False,
        comment="所属批次 ID（关联 t_sync_run 表）",
    )
    activity_id = Column(String(64), nullable=True, comment="源平台活动 ID")
    activity_name = Column(String(512), nullable=True, comment="活动名称")
    sport_type_raw = Column(String(64), nullable=True, comment="运动类型原始值")
    start_time_local = Column(
        DateTime(timezone=True), nullable=True, comment="运动开始时间（本地时区）"
    )
    start_time_gmt = Column(
        DateTime(timezone=True), nullable=True, comment="运动开始时间（UTC）"
    )
    distance_meters = Column(
        Numeric(12, 2), nullable=True, comment="距离（米）"
    )
    filename = Column(String(512), nullable=True, comment="上传文件名")
    status = Column(
        String(32),
        nullable=False,
        default="failed",
        comment="明细状态：synced/duplicate/failed",
    )
    message = Column(Text, nullable=True, comment="失败原因或平台返回摘要")
    target_activity_id = Column(String(64), nullable=True, comment="目标平台回执活动 ID")
    synced_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="本条同步完成时间",
    )

    run = relationship("SyncRun", back_populates="items")
