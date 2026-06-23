from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import relationship

from app.db.session import Base


class TaskResult(Base):
    """
    任务结果模型类，对应数据库中的 `t_task_result` 表。

    存储每个任务执行后的结果消息记录。
    """

    __tablename__ = "t_task_result"
    __table_args__ = (Index("idx_t_task_result_task_id", "task_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键 ID")
    task_id = Column(
        Integer,
        ForeignKey("t_task.id"),
        nullable=False,
        comment="关联的任务 ID（关联 t_task 表）",
    )
    task_messages = Column(
        Text,
        nullable=True,
        comment="任务执行结果消息内容",
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    # ---- 关系映射 ----
    task = relationship("Task", backref="results")
