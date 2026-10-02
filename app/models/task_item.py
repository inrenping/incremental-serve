from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import relationship

from app.db.session import Base


class TaskItem(Base):
    """
    任务子项模型类，对应数据库中的 `t_task_item` 表。

    一个任务（t_task）可包含多条「源 -> 目标」同步对，
    与任务上的多个触发小时（t_task.hours）构成笛卡尔积调度。
    """

    __tablename__ = "t_task_item"
    __table_args__ = (Index("idx_t_task_item_task_id", "task_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键 ID")
    task_id = Column(
        Integer,
        ForeignKey("t_task.id"),
        nullable=False,
        comment="关联的任务 ID（关联 t_task 表）",
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
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # ---- 关系映射 ----
    task = relationship("Task", backref="items")
    source_connect = relationship("BaseConnect", foreign_keys=[connect_source_id])
    target_connect = relationship("BaseConnect", foreign_keys=[connect_target_id])
