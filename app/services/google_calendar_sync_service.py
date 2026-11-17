"""把用户的睡眠（含小睡）与运动同步到专用 Google 日历。

设计要点：
- 只写一个由用户显式共享给服务账号的日历，绝不碰公开赛事日历。
- 事件 ID 由源记录确定性生成，重复跑不会重复创建；哈希不变则跳过更新。
- 只清理带本服务标记（extendedProperties.private.inc_source）且在同步窗口内的事件。
- 小睡优先按 t_sleep_nap 逐条生成带时段的事件；无明细但有汇总秒数时退回全天占位事件。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.sleep_daily import SleepDaily
from app.models.sleep_nap import SleepNap
from app.services.user_service import get_user_by_email, get_user_by_username

SYNC_SOURCE = "incremental-calendar"
DEFAULT_CALENDAR_ID = (
    "5d38a71f7b1996c2b62b40883741320d85ea81725d42e29fca695799de77365d"
    "@group.calendar.google.com"
)
SCOPES = ["https://www.googleapis.com/auth/calendar"]
MAX_RESULTS = 2500
ACTIVITY_SOURCE_PRIORITY = {
    "garmin": 0,
    "garmin_cn": 1,
    "coros": 2,
    "suunto": 3,
}


def _log(message: str) -> None:
    # 生产环境 logging 被 disable_existing_loggers 关掉，print 才能进 journald。
    print(f"[google-calendar-sync] {message}", flush=True)


def get_calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID") or DEFAULT_CALENDAR_ID


def get_user_timezone(user) -> ZoneInfo:
    name = getattr(user, "timezone", None) or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Shanghai")


def build_google_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    raw = settings.GOOGLE_ACCOUNT_SERVICE_JSON
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 未配置")
    credentials = Credentials.from_service_account_info(
        json.loads(raw), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def resolve_window(
    today: date,
    days: int | None = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
) -> tuple[date, date]:
    """返回 [start, end] 闭区间。

    优先级：year/month/day 指定单日 > start_date/end_date > days 回看窗口。
    """
    date_parts = (year, month, day)
    if any(part is not None for part in date_parts):
        if not all(part is not None for part in date_parts):
            raise ValueError("year/month/day 必须同时提供")
        target = date(int(year), int(month), int(day))
        return target, target
    if start_date:
        start = date.fromisoformat(start_date)
    else:
        start = today - timedelta(days=max(1, int(days or 7)) - 1)
    end = date.fromisoformat(end_date) if end_date else today
    if end < start:
        raise ValueError("end_date 不能早于 start_date")
    return start, end


def event_id(kind: str, key: str) -> str:
    """Google 建议 eventId 用 a-v/0-9，sha1 hex 刚好都是 0-9a-f。"""
    return hashlib.sha1(f"{SYNC_SOURCE}|{kind}|{key}".encode("utf-8")).hexdigest()


def content_hash(body: dict) -> str:
    watched = {
        "summary": body.get("summary"),
        "description": body.get("description"),
        "location": body.get("location"),
        "start": body.get("start"),
        "end": body.get("end"),
    }
    return hashlib.sha1(
        json.dumps(watched, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _attach_meta(body: dict, kind: str, key: str) -> dict:
    body["id"] = event_id(kind, key)
    private = {
        "inc_source": SYNC_SOURCE,
        "inc_kind": kind,
        "inc_key": key,
        "inc_hash": content_hash(body),
    }
    body["extendedProperties"] = {"private": private}
    # 睡眠/小睡不需要提醒，避免每天弹通知。
    body["reminders"] = {"useDefault": False}
    body["transparency"] = "transparent"
    return body


def _fmt_datetime(value: datetime, tz: ZoneInfo) -> dict:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    else:
        value = value.astimezone(tz)
    return {"dateTime": value.isoformat(), "timeZone": str(tz)}


def _fmt_duration(seconds: int | float | None) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}小时{minutes:02d}分" if minutes else f"{hours}小时"
    return f"{minutes}分钟"


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _activity_key(activity: BaseActivity) -> str:
    return f"{activity.source_type}:{activity.activity_id}"


def _activity_fingerprint(activity: BaseActivity, tz: ZoneInfo) -> tuple:
    """跨渠道去重：同一场活动按 开始分钟+时长+距离 判定为一条。"""
    start = activity.start_time_gmt or activity.start_time_local
    if start is None:
        return (str(activity.id),)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    else:
        start = start.astimezone(tz)
    duration = _to_int(activity.duration_seconds) or 0
    distance = _to_int(activity.distance_meters) or 0
    return (start.strftime("%Y%m%d%H%M"), duration, distance // 10)


def dedupe_activities(activities: list[BaseActivity], tz: ZoneInfo) -> list[BaseActivity]:
    groups: dict[tuple, BaseActivity] = {}
    for activity in activities:
        fingerprint = _activity_fingerprint(activity, tz)
        current = groups.get(fingerprint)
        if current is None:
            groups[fingerprint] = activity
            continue
        current_rank = ACTIVITY_SOURCE_PRIORITY.get(current.source_type, 99)
        new_rank = ACTIVITY_SOURCE_PRIORITY.get(activity.source_type, 99)
        if new_rank < current_rank:
            groups[fingerprint] = activity
    return list(groups.values())


def build_activity_event(activity: BaseActivity, tz: ZoneInfo) -> dict | None:
    start = activity.start_time_gmt or activity.start_time_local
    if start is None:
        return None
    start_dt = _fmt_datetime(start, tz)

    if activity.end_time_gmt is not None:
        end_value = activity.end_time_gmt
    else:
        seconds = _to_int(activity.duration_seconds) or _to_int(
            activity.moving_duration_seconds
        )
        if not seconds:
            return None
        base = start if start.tzinfo else start.replace(tzinfo=tz)
        end_value = base + timedelta(seconds=seconds)
    end_dt = _fmt_datetime(end_value, tz)

    distance = _to_int(activity.distance_meters)
    sport = (activity.sport_type_raw or "").strip()
    name = (activity.activity_name or sport or "运动").strip()
    if distance and distance > 0:
        summary = f"{name} · {distance / 1000:.2f} km"
    else:
        summary = name

    lines = [f"来源：{activity.source_type}"]
    if sport:
        lines.append(f"运动类型：{sport}")
    duration = _to_int(activity.duration_seconds)
    if duration:
        lines.append(f"时长：{_fmt_duration(duration)}")
    if distance:
        lines.append(f"距离：{distance / 1000:.2f} km")
    if activity.average_hr:
        lines.append(f"平均心率：{activity.average_hr}")
    if activity.location_name:
        lines.append(f"地点：{activity.location_name}")
    if activity.elevation_gain:
        lines.append(f"爬升：{_to_int(activity.elevation_gain)} m")
    lines.append(f"原始 ID：{activity.activity_id}")

    body = {
        "summary": summary,
        "description": "\n".join(lines),
        "start": start_dt,
        "end": end_dt,
    }
    if activity.location_name:
        body["location"] = activity.location_name
    return _attach_meta(body, "activity", _activity_key(activity))


def build_sleep_event(daily: SleepDaily, tz: ZoneInfo) -> dict | None:
    if daily.sleep_start_at is None or daily.sleep_end_at is None:
        return None
    if daily.sleep_end_at <= daily.sleep_start_at:
        return None

    seconds = _to_int(daily.sleep_time_seconds) or 0
    score = daily.sleep_score
    summary = f"睡眠 {_fmt_duration(seconds)}"
    if score:
        summary += f" · 得分 {score}"

    lines = ["来源：Garmin / incremental"]
    for label, field in (
        ("深睡", "deep_sleep_seconds"),
        ("浅睡", "light_sleep_seconds"),
        ("REM", "rem_sleep_seconds"),
        ("清醒", "awake_sleep_seconds"),
    ):
        value = _to_int(getattr(daily, field))
        if value:
            lines.append(f"{label}：{_fmt_duration(value)}")
    lines.append(f"日历日期（起床日）：{daily.calendar_date}")

    body = {
        "summary": summary,
        "description": "\n".join(lines),
        "start": _fmt_datetime(daily.sleep_start_at, tz),
        "end": _fmt_datetime(daily.sleep_end_at, tz),
    }
    return _attach_meta(body, "sleep", str(daily.id))


def build_nap_event(daily: SleepDaily) -> dict | None:
    """小睡汇总占位（兜底）：当天有小睡秒数但没有逐条明细时才用。"""
    seconds = _to_int(daily.nap_time_seconds) or 0
    if seconds <= 0 or daily.calendar_date is None:
        return None
    day = daily.calendar_date
    body = {
        "summary": f"小睡合计 {_fmt_duration(seconds)}",
        "description": (
            f"当天小睡共 {_fmt_duration(seconds)}。\n"
            "该日只有汇总数据、没有逐条小睡明细，因此用全天事件占位。"
        ),
        "start": {"date": day.isoformat()},
        "end": {"date": (day + timedelta(days=1)).isoformat()},
    }
    return _attach_meta(body, "nap-summary", str(daily.id))


def build_nap_detail_event(nap: SleepNap, daily: SleepDaily, tz: ZoneInfo) -> dict | None:
    """逐条小睡事件：带具体起止时段。"""
    if nap.start_at is None or nap.end_at is None or nap.end_at <= nap.start_at:
        return None
    seconds = _to_int(nap.duration_seconds) or int(
        (nap.end_at - nap.start_at).total_seconds()
    )
    summary = f"小睡 {_fmt_duration(seconds)}"
    lines = ["来源：Garmin / incremental"]
    if nap.nap_feedback:
        lines.append(f"小睡反馈：{nap.nap_feedback}")
    if daily.calendar_date is not None:
        lines.append(f"日历日期（起床日）：{daily.calendar_date}")
    body = {
        "summary": summary,
        "description": "\n".join(lines),
        "start": _fmt_datetime(nap.start_at, tz),
        "end": _fmt_datetime(nap.end_at, tz),
    }
    # key 用 daily_id + 开始时刻：小睡行每次同步会重建（id 不稳定），内容 key 才能保证幂等
    return _attach_meta(body, "nap", f"{daily.id}:{nap.start_at.isoformat()}")


def build_desired_events(
    sleeps: list[SleepDaily],
    activities: list[BaseActivity],
    naps_by_daily: dict[int, list],
    tz: ZoneInfo,
) -> dict[str, dict]:
    desired: dict[str, dict] = {}
    for activity in dedupe_activities(activities, tz):
        event = build_activity_event(activity, tz)
        if event:
            desired[event["id"]] = event
    for daily in sleeps:
        event = build_sleep_event(daily, tz)
        if event:
            desired[event["id"]] = event
        nap_rows = naps_by_daily.get(daily.id) or []
        if nap_rows:
            for nap in nap_rows:
                nap_event = build_nap_detail_event(nap, daily, tz)
                if nap_event:
                    desired[nap_event["id"]] = nap_event
        else:
            # 无逐条明细时才用汇总占位，有明细后占位事件会被对账自动清掉
            nap_event = build_nap_event(daily)
            if nap_event:
                desired[nap_event["id"]] = nap_event
    return desired


def _query_sleeps(db: Session, user_id: int, start: date, end: date) -> list[SleepDaily]:
    return (
        db.query(SleepDaily)
        .filter(
            SleepDaily.user_id == user_id,
            SleepDaily.calendar_date >= start,
            SleepDaily.calendar_date <= end,
        )
        .all()
    )


def _query_naps(db: Session, daily_ids: list[int]) -> dict[int, list[SleepNap]]:
    """按 daily_id 分组取小睡明细。"""
    if not daily_ids:
        return {}
    grouped: dict[int, list[SleepNap]] = {}
    for nap in (
        db.query(SleepNap)
        .filter(SleepNap.daily_id.in_(daily_ids))
        .order_by(SleepNap.daily_id, SleepNap.start_at)
        .all()
    ):
        grouped.setdefault(nap.daily_id, []).append(nap)
    return grouped


def _query_activities(
    db: Session, user_id: int, start: date, end: date
) -> list[BaseActivity]:
    """只取该用户主数据源（master=True 且激活）连接下的运动。

    口径与 main_activity_service 一致：跨渠道同步过来的重复活动不进日历，
    日历只反映用户指定的那一个主账号。
    """
    master_connect_ids = [
        row[0]
        for row in db.query(BaseConnect.id)
        .filter(
            BaseConnect.user_id == user_id,
            BaseConnect.master == True,  # noqa: E712
            BaseConnect.is_active == True,  # noqa: E712
        )
        .all()
    ]
    if not master_connect_ids:
        _log(f"用户 {user_id} 没有主数据源连接，跳过运动同步")
        return []

    return (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == user_id,
            BaseActivity.base_connect_id.in_(master_connect_ids),
            or_(
                BaseActivity.start_time_local.between(
                    datetime.combine(start, time.min),
                    datetime.combine(end, time.max),
                ),
                and_(
                    BaseActivity.start_time_local.is_(None),
                    BaseActivity.start_time_gmt >= datetime.combine(
                        start, time.min, tzinfo=timezone.utc
                    ),
                    BaseActivity.start_time_gmt <= datetime.combine(
                        end, time.max, tzinfo=timezone.utc
                    ),
                ),
            ),
        )
        .all()
    )


def _fetch_existing(
    service, calendar_id: str, start: date, end: date, tz: ZoneInfo
) -> dict[str, dict]:
    params = dict(
        calendarId=calendar_id,
        timeMin=datetime.combine(
            start - timedelta(days=2), time.min, tzinfo=tz
        ).isoformat(),
        timeMax=datetime.combine(
            end + timedelta(days=3), time.max, tzinfo=tz
        ).isoformat(),
        singleEvents=True,
        maxResults=MAX_RESULTS,
    )
    events: dict[str, dict] = {}
    try:
        events.update(_fetch_with_pages(service, params, filtered=True))
    except Exception as exc:
        # 某些日历不支持 privateExtendedProperty 过滤，退回全量拉取后本地筛选。
        _log(f"extendedProperty 过滤失败，退回全量拉取：{exc}")
        events.update(_fetch_with_pages(service, params, filtered=False))
    return {
        event_id: event
        for event_id, event in events.items()
        if (event.get("extendedProperties") or {})
        .get("private", {})
        .get("inc_source")
        == SYNC_SOURCE
    }


def _fetch_with_pages(service, params: dict, filtered: bool) -> dict[str, dict]:
    request_params = dict(params)
    if filtered:
        request_params["privateExtendedProperty"] = f"inc_source={SYNC_SOURCE}"
    result: dict[str, dict] = {}
    page_token = None
    while True:
        if page_token:
            request_params["pageToken"] = page_token
        response = service.events().list(**request_params).execute()
        for event in response.get("items", []):
            if event.get("id"):
                result[event["id"]] = event
        page_token = response.get("nextPageToken")
        if not page_token:
            return result


def _event_in_window(event: dict, start: date, end: date) -> bool:
    start_info = event.get("start") or {}
    raw = start_info.get("date") or start_info.get("dateTime")
    if not raw:
        return False
    try:
        day = date.fromisoformat(raw[:10])
    except ValueError:
        return False
    return start <= day <= end


def sync_calendar(
    db: Session,
    user,
    days: int = 7,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    service=None,
    calendar_id: str | None = None,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
) -> dict:
    """执行一次对账式同步。service 可注入，便于离线测试。"""
    tz = get_user_timezone(user)
    today = datetime.now(tz).date()
    start, end = resolve_window(
        today,
        days=days,
        start_date=start_date,
        end_date=end_date,
        year=year,
        month=month,
        day=day,
    )
    calendar_id = calendar_id or get_calendar_id()

    sleeps = _query_sleeps(db, user.id, start, end)
    naps_by_daily = _query_naps(db, [daily.id for daily in sleeps])
    activities = _query_activities(db, user.id, start, end)
    desired = build_desired_events(sleeps, activities, naps_by_daily, tz)

    if service is None:
        service = build_google_service()
    existing = _fetch_existing(service, calendar_id, start, end, tz)

    stats = {
        "calendar_id": calendar_id,
        "window": [start.isoformat(), end.isoformat()],
        "sleep_days": len(sleeps),
        "naps": sum(len(rows) for rows in naps_by_daily.values()),
        "activities": len(activities),
        "desired_events": len(desired),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "deleted": 0,
        "dry_run": bool(dry_run),
    }

    for event_id, body in desired.items():
        current = existing.get(event_id)
        if current is not None:
            current_hash = (
                (current.get("extendedProperties") or {})
                .get("private", {})
                .get("inc_hash")
            )
            if current_hash == body["extendedProperties"]["private"]["inc_hash"]:
                stats["skipped"] += 1
                continue
            if not dry_run:
                payload = {k: v for k, v in body.items() if k != "id"}
                service.events().update(
                    calendarId=calendar_id, eventId=event_id, body=payload
                ).execute()
            stats["updated"] += 1
        else:
            if not dry_run:
                service.events().insert(calendarId=calendar_id, body=body).execute()
            stats["created"] += 1

    for event_id, event in existing.items():
        if event_id in desired or not _event_in_window(event, start, end):
            continue
        if not dry_run:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        stats["deleted"] += 1

    _log(json.dumps(stats, ensure_ascii=False))
    return stats


def sync_calendar_for_cron_user(
    db: Session,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    dry_run: bool = False,
) -> dict:
    """定时任务入口：按 CRON_SYNC_USER_EMAIL 定位目标用户。

    不传年月日时同步「用户时区的今天」（上午 8 点昨晚睡眠已落库）。
    """
    email = os.getenv("CRON_SYNC_USER_EMAIL", "inrenping")
    user = get_user_by_username(db, email) or get_user_by_email(db, email)
    if user is None:
        raise RuntimeError(f"定时任务目标用户 {email} 不存在")

    if year is None and month is None and day is None:
        today = datetime.now(get_user_timezone(user)).date()
        year, month, day = today.year, today.month, today.day

    return sync_calendar(
        db=db,
        user=user,
        year=year,
        month=month,
        day=day,
        dry_run=dry_run,
    )
