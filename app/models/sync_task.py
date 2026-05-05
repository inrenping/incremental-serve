from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.session import Base


class SyncTask(Base):
    """
    数据同步任务实体，对应数据库表 `t_sync_task`。
    用于记录跨平台数据同步的具体任务及执行状态。

    Attributes:
        id: 自增主键
        user_id: 关联 t_users 表的外键
        source_platform: 源平台名称（如 A平台、B平台等）
        source_id: 源平台的数据ID
        target_platform: 目标平台名称
        target_id: 目标平台的数据ID
        activity_type: 活动/数据类型
        sync_status: 同步状态（-1: 待处理/默认, 0: 失败, 1: 成功等）
        sync_result_info: 同步结果详情或错误信息
        created_at: 任务创建时间
        synced_at: 实际同步完成的时间
    """

    __tablename__ = "t_sync_task"
    __table_args__ = (
        UniqueConstraint("user_id", "source_platform", "source_id", name="idx_unique_sync_task"),
        Index("idx_sync_task_user_status", "user_id", "sync_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(
        Integer,
        ForeignKey("t_users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    source_platform = Column(String(50), nullable=False, comment="源平台名称")
    source_id = Column(Integer, nullable=False, comment="源平台的数据ID")
    target_platform = Column(String(50), nullable=False, comment="目标平台名称")
    target_id = Column(Integer, nullable=False, comment="目标平台的数据ID")
    activity_type = Column(String(50), nullable=True, comment="活动/数据类型")
    sync_status = Column(SmallInteger, default=-1, comment="同步状态（-1: 待处理/默认）")
    sync_result_info = Column(Text, nullable=True, comment="同步结果详情或错误信息")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
        comment="任务创建时间",
    )
    synced_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="实际同步完成的时间",
    )

    user = relationship("User")