import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.models.task import Task
from app.models.task_result import TaskResult
from app.models.user import User
from app.services import quick_sync_service

logger = logging.getLogger(__name__)


def run_cron_execute2_internal() -> None:
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        tasks = db.query(Task).filter(Task.is_active == True).all()

        executed_count = 0
        for task in tasks:
            user = db.query(User).filter(User.id == task.user_id).first()
            if not user:
                continue

            user_tz = user.timezone or "Asia/Shanghai"
            local_hour = now_utc.astimezone(ZoneInfo(user_tz)).hour
            if task.hour != local_hour:
                continue

            try:
                result = quick_sync_service.run_quick_sync(
                    db=db,
                    current_user=user,
                    source_id=task.connect_source_id,
                    target_id=task.connect_target_id,
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
        minute=0,
        id="cron_execute2_hourly",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    logger.info(
        "[scheduler] registered jobs: cron_execute2_hourly (every hour at minute 0, UTC)"
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
