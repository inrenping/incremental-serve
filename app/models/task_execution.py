from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BIGSERIAL

from app.db.session import Base


class TaskExecution(Base):
    """
    任务执行幂等表。

    每条记录代表「某个任务在某个时间槽位已被执行过一次」。
    主键 (task_id, run_slot) 上的唯一约束充当幂等闸：调度器、外部无鉴权接口、
    以及多 worker 实例之间，即使重复尝试执行同一槽位，也只有一个能插入成功，
    其余视为「已执行」直接跳过，从而避免任务被重复触发。

    run_slot 形如：
      - 定时执行： "2026-10-11 08:23"（任务本地时区的 日期 小时:分钟）
      - 手动触发： "manual-20261011082345-12"（cron-execute2 强制触发，永不参与定时去重）
    """

    __tablename__ = "t_task_execution"
    __table_args__ = (
        UniqueConstraint("task_id", "run_slot", name="uk_task_execution_slot"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(
        Integer,
        nullable=False,
        comment="任务 ID（关联 t_task.id）",
    )
    run_slot = Column(
        String(20),
        nullable=False,
        comment="执行槽位标识，定时为 本地日期 小时:分钟，手动为 manual-时间戳-任务id",
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="占坑时间",
    )
