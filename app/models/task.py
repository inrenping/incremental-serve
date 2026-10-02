from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class Task(Base):
    """
    任务模型类，对应数据库中的 `t_task` 表。

    存储用户创建的数据同步任务，包含源和目标连接配置及调度信息。
    """

    __tablename__ = "t_task"
    __table_args__ = (Index("idx_t_task_user_active", "user_id", "is_active"),)

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键 ID")
    user_id = Column(
        Integer,
        ForeignKey("t_users.id"),
        nullable=False,
        comment="用户 ID（关联 t_users 表）",
    )
    connect_source_id = Column(
        Integer,
        ForeignKey("t_base_connect.id"),
        nullable=True,
        comment="[Deprecated] 旧版单同步对源端连接 ID，迁移后由 t_task_item 取代；"
        "新建任务仍写入首条同步对，便于旧代码/旧数据兼容",
    )
    connect_target_id = Column(
        Integer,
        ForeignKey("t_base_connect.id"),
        nullable=True,
        comment="[Deprecated] 旧版单同步对目标端连接 ID，迁移后由 t_task_item 取代；"
        "新建任务仍写入首条同步对，便于旧代码/旧数据兼容",
    )
    hour = Column(
        Integer,
        nullable=True,
        comment="[Deprecated] 旧版单小时字段，迁移后由 hours 取代",
    )
    hours = Column(
        JSON,
        nullable=True,
        comment="任务触发小时列表（如 [8, 20]），本地时区 0-23",
    )
    is_active = Column(
        Boolean,
        default=True,
        nullable=True,
        comment="任务是否激活",
    )
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
    source_connect = relationship("BaseConnect", foreign_keys=[connect_source_id])
    target_connect = relationship("BaseConnect", foreign_keys=[connect_target_id])
