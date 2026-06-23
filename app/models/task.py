from datetime import datetime, timezone
from sqlalchemy import (
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
        nullable=False,
        comment="源连接配置 ID（关联 t_base_connect 表）",
    )
    connect_target_id = Column(
        Integer,
        ForeignKey("t_base_connect.id"),
        nullable=False,
        comment="目标连接配置 ID（关联 t_base_connect 表）",
    )
    hour = Column(
        Integer,
        nullable=False,
        comment="任务执行的小时数（如每隔 N 小时同步一次）",
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
