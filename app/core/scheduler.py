import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.models.task import Task
from app.models.task_item import TaskItem
from app.models.task_result import TaskResult
from app.models.user import User
from app.services import quick_sync_service
from app.services import garmin_service, garmin_metrics_service, main_activity_service
from app.services.base_activity_service import batch_upload_fit_to_storage

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


# --------------------------------------------------------------------------
# 周期同步任务（替代 GitHub Actions 定时 curl，改为服务器内置调度）
#
# 为什么搬进来：GitHub Actions 的 schedule: 在免费/标准档会被节流、延迟甚至静默丢弃，
# 而且从 GitHub 共享出口 IP 打 Garmin 容易被限流；心率接口此前还因 Runner 用 UTC
# 算日期导致错一天。改成在 VPS 本进程用 APScheduler 跑，时区/IP 都正确，且能遍历
# 所有「主账号是佳明」的用户（不再只同步固定账号 inrenping）。
#
# 各任务独立开 DB 会话、单用户失败隔离、带 misfire_grace/coalesce 保护。
# 时间均为 UTC；函数内部按每个用户的时区推算「昨天/今天」，跨时区都覆盖。
# 仅在生产环境注册，避免本地/测试机拿真实佳明凭证去打 Garmin。
# --------------------------------------------------------------------------


def _safe_job(name: str, fn) -> None:
    """包一层 DB 会话与异常兜底：单个周期任务失败不影响调度器其他任务。"""
    db = SessionLocal()
    try:
        logger.info(f"[scheduler] job {name} start")
        fn(db)
        db.commit()
        logger.info(f"[scheduler] job {name} done")
    except Exception:
        logger.exception(f"[scheduler] job {name} failed")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _job_sync_heart_rate_all() -> None:
    _safe_job(
        "sync_heart_rate_all",
        lambda db: garmin_service.sync_daily_heart_rate_for_all_masters(db),
    )


def _job_sync_sleep_all() -> None:
    _safe_job(
        "sync_sleep_all",
        lambda db: garmin_service.sync_daily_sleep_for_all_masters(db),
    )


def _job_sync_fitness_metrics_all() -> None:
    _safe_job(
        "sync_fitness_metrics_all",
        lambda db: garmin_metrics_service.sync_fitness_metrics_for_all_masters(db),
    )


def _job_sync_main_activity_all() -> None:
    _safe_job(
        "sync_main_activity_all",
        lambda db: main_activity_service.sync_base_to_main_activity_all(db, days=7),
    )


def _job_refresh_garmin_count() -> None:
    _safe_job(
        "refresh_garmin_count",
        lambda db: garmin_service.refresh_garmin_activity_count(db),
    )


def _job_backup_fit() -> None:
    _safe_job(
        "backup_fit",
        lambda db: batch_upload_fit_to_storage(db, limit=5),
    )


def _register_periodic_sync_jobs() -> None:
    """注册周期同步任务。所有时间均为 UTC。"""
    # 心率：上海时间每天 06:00–24:00 每小时一次（对应 UTC 22:00/23:00 前一天 + 00:00–16:00 当天）。
    # 凌晨 01:00–05:00 上海时间不跑（跳过睡眠时段）。运行时按各用户时区算昨天+今天。
    scheduler.add_job(
        _job_sync_heart_rate_all,
        trigger="cron",
        hour="0-16,22,23",
        id="sync_heart_rate_all",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    # 睡眠：每天 23:00 / 01:00 UTC（对应上海 07:00 / 09:00），运行时按用户时区算当天
    scheduler.add_job(
        _job_sync_sleep_all,
        trigger="cron",
        hour="23,1",
        id="sync_sleep_all",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    # 体能指标：每天 23:00 UTC（对应上海 07:00），遍历所有主账号为佳明的用户
    scheduler.add_job(
        _job_sync_fitness_metrics_all,
        trigger="cron",
        hour="23",
        id="sync_fitness_metrics_all",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    # 主活动：每 6 小时一次，把各主数据源近 7 天活动同步到 t_main_activity
    scheduler.add_job(
        _job_sync_main_activity_all,
        trigger="cron",
        hour="*/6",
        id="sync_main_activity_all",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    # 刷新佳明活动计数：每天 16:00 UTC（对应上海 00:00）
    scheduler.add_job(
        _job_refresh_garmin_count,
        trigger="cron",
        hour="16",
        id="refresh_garmin_count",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    # # 备份 FIT 到对象存储：每天 00:00 / 12:00 UTC（对应上海 08:00 / 20:00）
    # scheduler.add_job(
    #     _job_backup_fit,
    #     trigger="cron",
    #     hour="0,12",
    #     id="backup_fit",
    #     replace_existing=True,
    #     misfire_grace_time=3600,
    #     coalesce=True,
    #     max_instances=1,
    # )
    logger.info(
        "[scheduler] registered periodic sync jobs: "
        "sync_heart_rate_all(0-16,22,23), sync_sleep_all(23,1), "
        "sync_fitness_metrics_all(23), sync_main_activity_all(*/6), "
        "refresh_garmin_count(16), backup_fit(0,12) [UTC]"
    )


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

    # 周期同步任务仅在生产环境注册：本地/测试机不应拿真实佳明凭证去打 Garmin。
    if os.getenv("APP_ENV") == "production":
        _register_periodic_sync_jobs()
    else:
        logger.info("[scheduler] skip periodic sync jobs (APP_ENV != production)")


def start_scheduler() -> None:
    if not scheduler.running:
        register_jobs()
        scheduler.start()
        logger.info("[scheduler] started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("[scheduler] stopped")
