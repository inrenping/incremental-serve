from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.base_connect import BaseConnect
from app.models.sync_run import SyncRun, SyncRunItem
from app.models.user import User


def start_run(
    db: Session,
    user: User,
    source_connect: BaseConnect,
    target_connect: BaseConnect,
    window_size: int,
    task_id: int | None = None,
    trigger_mode: str = "manual",
) -> SyncRun:
    """开启一次同步批次，写入一条 run 记录。"""
    run = SyncRun(
        user_id=user.id,
        source_connect_id=source_connect.id,
        target_connect_id=target_connect.id,
        task_id=task_id,
        trigger_mode=trigger_mode,
        window_size=window_size,
        source_platform=source_connect.source_type,
        target_platform=target_connect.source_type,
        source_account=source_connect.account,
        target_account=target_connect.account,
        status="error",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_run(
    db: Session,
    run: SyncRun,
    status: str,
    error_message: str | None = None,
) -> SyncRun:
    """结束批次，更新汇总与耗时。"""
    run.status = status
    run.error_message = error_message
    run.finished_at = datetime.now(timezone.utc)
    if run.started_at:
        run.duration_ms = int(
            (run.finished_at - run.started_at).total_seconds() * 1000
        )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _derive_status(run: SyncRun) -> str:
    if run.diff_count == 0:
        return "no_diff"
    if run.failed_count == 0 and run.uploaded_count > 0:
        return "success"
    if run.uploaded_count > 0 or run.skipped_count > 0:
        return "partial"
    return "failed"


def finalize(db: Session, run: SyncRun, error_message: str | None = None) -> SyncRun:
    """依据计数自动判定批次最终状态。"""
    if error_message:
        return finish_run(db, run, "error", error_message)
    return finish_run(db, run, _derive_status(run))


def record_item(
    db: Session,
    run_id: int,
    activity: dict,
    status: str,
    message: str | None = None,
    target_activity_id: str | None = None,
) -> SyncRunItem:
    """落一条明细记录，并同步累加批次计数。"""
    run = db.query(SyncRun).filter(SyncRun.id == run_id).first()
    item = SyncRunItem(
        run_id=run_id,
        activity_id=str(activity.get("activity_id")),
        activity_name=activity.get("activity_name") or "",
        sport_type_raw=activity.get("sport_type_raw"),
        start_time_local=activity.get("start_time_local"),
        start_time_gmt=activity.get("start_time_gmt"),
        distance_meters=activity.get("distance_meters"),
        filename=activity.get("_filename"),
        status=status,
        message=message,
        target_activity_id=target_activity_id,
        synced_at=datetime.now(timezone.utc),
    )
    db.add(item)
    if run:
        if status == "synced":
            run.uploaded_count = (run.uploaded_count or 0) + 1
        elif status == "duplicate":
            run.skipped_count = (run.skipped_count or 0) + 1
        elif status == "failed":
            run.failed_count = (run.failed_count or 0) + 1
        db.add(run)
    db.commit()
    db.refresh(item)
    return item


def list_runs(db: Session, user: User, limit: int = 10, page: int = 1) -> tuple[list[SyncRun], int]:
    query = db.query(SyncRun).filter(SyncRun.user_id == user.id)
    total = query.count()
    items = (
        query.order_by(desc(SyncRun.created_at))
        .offset((max(page, 1) - 1) * limit)
        .limit(limit)
        .all()
    )
    return items, total


def get_region_map(db: Session, runs: list[SyncRun]) -> dict[int, str | None]:
    """按 connect_id 回查批次源/目标账号的区域。

    region 只落在 t_base_connect（garmin: cn/global、suunto: cn/intl、
    coros: coros 即无区域概念），t_sync_run 本身不冗余该字段，
    所以这里用 source_connect_id / target_connect_id 一次性批量回查，
    历史批次也能正确展示。

    Args:
        db (Session): 数据库会话。
        runs (list[SyncRun]): 当前页的批次列表。
    Returns:
        dict[int, str | None]: connect_id -> region。
    """
    connect_ids = {
        cid
        for r in runs
        for cid in (r.source_connect_id, r.target_connect_id)
        if cid
    }
    if not connect_ids:
        return {}
    rows = (
        db.query(BaseConnect.id, BaseConnect.region)
        .filter(BaseConnect.id.in_(connect_ids))
        .all()
    )
    return {cid: region for cid, region in rows}


def get_run(db: Session, user: User, run_id: int) -> SyncRun | None:
    return (
        db.query(SyncRun)
        .filter(SyncRun.id == run_id, SyncRun.user_id == user.id)
        .first()
    )


def list_items(db: Session, run_id: int) -> list[SyncRunItem]:
    return (
        db.query(SyncRunItem)
        .filter(SyncRunItem.run_id == run_id)
        .order_by(SyncRunItem.id)
        .all()
    )
