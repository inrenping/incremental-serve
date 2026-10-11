import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.models.task import Task
from app.models.task_item import TaskItem
from app.models.task_result import TaskResult
from app.models.user import User
from app.services import quick_sync_service

logger = logging.getLogger(__name__)


def _resolve_task_hours(task: Task) -> list:
    """兼容新旧字段：优先 hours 列表，退回旧的单 hour 字段"""
    if task.hours:
        return task.hours
    return [task.hour] if task.hour is not None else []


def _claim_slot(db, task_id: int, run_slot: str) -> bool:
    """幂等占坑：同一 (task_id, run_slot) 只返回 True 一次。

    用 DB 唯一约束解决「应用内调度器 / 外部无鉴权接口 / 多 worker 实例」之间的并发竞态。
    采用 INSERT ... ON CONFLICT DO NOTHING，靠 rowcount 判断（1=新占坑，0=已被占），
    不抛 IntegrityError，因此不会触发会话 rollback、影响同一事务内其余任务的执行记录写入。
    """
    from sqlalchemy import text

    res = db.execute(
        text(
            "INSERT INTO t_task_execution (task_id, run_slot) "
            "VALUES (:task_id, :run_slot) ON CONFLICT (task_id, run_slot) DO NOTHING"
        ),
        {"task_id": task_id, "run_slot": run_slot},
    )
    return res.rowcount == 1


def _build_run_slot(local_dt, force: bool, task_id: int) -> str:
    """构造执行槽位标识。定时执行锚定到『本地日期 小时:分钟』，手动触发用独立前缀。"""
    if force:
        return f"manual-{local_dt.strftime('%Y%m%d%H%M%S')}-{task_id}"
    return f"{local_dt.strftime('%Y-%m-%d')} {local_dt.hour:02d}:{local_dt.minute:02d}"


def run_cron_execute2_internal(force_minute: bool = False) -> None:
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        tasks = db.query(Task).filter(Task.is_active == True).all()

        executed_count = 0
        for task in tasks:
            user = db.query(User).filter(User.id == task.user_id).first()
            if not user:
                continue

            task_hours = _resolve_task_hours(task)
            if not task_hours:
                continue

            user_tz = user.timezone or "Asia/Shanghai"
            local_dt = now_utc.astimezone(ZoneInfo(user_tz))
            if local_dt.hour not in task_hours:
                continue

            # 错峰：定时模式下只在「定死的分钟」命中才执行；手动触发(force)忽略分钟。
            target_minute = task.minute if task.minute is not None else 0
            if not force_minute and local_dt.minute != target_minute:
                continue

            # 幂等占坑：同一槽位只执行一次，杜绝重复触发。
            run_slot = _build_run_slot(local_dt, force_minute, task.id)
            if not _claim_slot(db, task.id, run_slot):
                continue

            items = db.query(TaskItem).filter(TaskItem.task_id == task.id).all()
            for item in items:
                try:
                    result = quick_sync_service.run_quick_sync(
                        db=db,
                        current_user=user,
                        source_id=item.connect_source_id,
                        target_id=item.connect_target_id,
                        count=10,
                        task_id=task.id,
                        trigger_mode="scheduled",
                    )
                    messages = json.dumps(result, ensure_ascii=False)
                except Exception as e:
                    logger.exception(f"[scheduler] task {task.id} execute failed")
                    messages = json.dumps(
                        {"status": "error", "message": f"执行异常: {str(e)}"},
                        ensure_ascii=False,
                    )

                task_result = TaskResult(
                    task_id=task.id,
                    task_messages=messages,
                )
                db.add(task_result)
                executed_count += 1

        db.commit()
        logger.info(
            f"[scheduler] cron-execute2 done: checked {len(tasks)} tasks, executed {executed_count}"
        )
    except Exception:
        logger.exception("[scheduler] cron-execute2 unexpected error")
        db.rollback()
    finally:
        db.close()


scheduler = BackgroundScheduler(timezone="UTC")


def register_jobs() -> None:
    scheduler.add_job(
        run_cron_execute2_internal,
        trigger="cron",
        minute="*",
        id="cron_execute2_minutely",
        replace_existing=True,
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )
    logger.info(
        "[scheduler] registered jobs: cron_execute2_minutely (every minute, UTC)"
    )


def start_scheduler() -> None:
    if not scheduler.running:
        register_jobs()
        scheduler.start()
        logger.info("[scheduler] started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("[scheduler] stopped")
