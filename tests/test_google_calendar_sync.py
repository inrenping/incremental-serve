"""Google 日历同步的纯函数/离线对账测试（不访问真实 Google API）。"""

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")

from app.models.base_activity import BaseActivity
from app.models.sleep_daily import SleepDaily
from app.services import google_calendar_sync_service as svc


def _tz():
    return svc.ZoneInfo("Asia/Shanghai")


def test_resolve_window_accepts_year_month_day():
    resolved = svc.resolve_window(
        date(2026, 11, 17), year=2026, month=11, day=16
    )
    assert resolved == (date(2026, 11, 16), date(2026, 11, 16))
    try:
        svc.resolve_window(date(2026, 11, 17), year=2026, month=11)
    except ValueError:
        pass
    else:
        raise AssertionError("年月日必须同时提供")


def test_event_id_stable_and_safe():
    first = svc.event_id("activity", "garmin:123")
    assert first == svc.event_id("activity", "garmin:123")
    assert first != svc.event_id("sleep", "garmin:123")
    assert len(first) == 40
    assert set(first) <= set("0123456789abcdef")


def test_activity_event_uses_user_timezone_and_distance():
    activity = SimpleNamespace(
        id=1,
        source_type="garmin",
        activity_id="123",
        activity_name="晨跑",
        sport_type_raw="RUNNING",
        start_time_gmt=datetime(2026, 11, 16, 23, 30, tzinfo=timezone.utc),
        start_time_local=datetime(2026, 11, 17, 7, 30),
        end_time_gmt=datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc),
        distance_meters=10200,
        duration_seconds=5400,
        moving_duration_seconds=5300,
        average_hr=145,
        location_name="宁波",
        elevation_gain=32,
    )
    event = svc.build_activity_event(activity, _tz())
    assert event["summary"].startswith("晨跑")
    assert "10.20 km" in event["summary"]
    assert event["start"]["dateTime"].startswith("2026-11-17T07:30:00+08:00")
    assert event["end"]["dateTime"].startswith("2026-11-17T09:00:00+08:00")
    assert event["location"] == "宁波"
    assert event["extendedProperties"]["private"]["inc_kind"] == "activity"


def test_sleep_and_nap_events():
    daily = SimpleNamespace(
        id=7,
        calendar_date=date(2026, 11, 17),
        sleep_start_at=datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc),
        sleep_end_at=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc),
        sleep_time_seconds=27000,
        nap_time_seconds=1800,
        sleep_score=80,
        deep_sleep_seconds=7200,
        light_sleep_seconds=12600,
        rem_sleep_seconds=7200,
        awake_sleep_seconds=600,
    )
    sleep = svc.build_sleep_event(daily, _tz())
    assert sleep["start"]["dateTime"].startswith("2026-11-16T23:30:00+08:00")
    assert sleep["end"]["dateTime"].startswith("2026-11-17T07:00:00+08:00")
    assert "得分 80" in sleep["summary"]

    nap = svc.build_nap_event(daily)
    assert nap["start"] == {"date": "2026-11-17"}
    assert nap["end"] == {"date": "2026-11-18"}
    assert "30分钟" in nap["summary"]


def test_dedupe_activities_prefers_garmin():
    def activity(source, ident, distance, start_hour=8):
        return SimpleNamespace(
            id=ident,
            source_type=source,
            activity_id=str(ident),
            activity_name="跑步",
            sport_type_raw="RUNNING",
            start_time_gmt=None,
            start_time_local=datetime(2026, 11, 17, start_hour, 0),
            end_time_gmt=None,
            distance_meters=distance,
            duration_seconds=1800,
            moving_duration_seconds=1800,
            average_hr=None,
            location_name=None,
            elevation_gain=None,
        )

    kept = svc.dedupe_activities(
        [activity("coros", 2, 5000), activity("garmin", 1, 5000), activity("garmin", 3, 7000)],
        _tz(),
    )
    assert len(kept) == 2
    assert {item.source_type for item in kept} == {"garmin"}
    assert {svc._to_int(item.distance_meters) for item in kept} == {5000, 7000}


class FakeEvents:
    def __init__(self):
        self.store = {}
        self.writes = []

    def list(self, **params):
        return self

    def insert(self, calendarId, body):
        self.writes.append(("insert", body["id"]))
        self.store[body["id"]] = dict(body)
        return self

    def update(self, calendarId, eventId, body):
        self.writes.append(("update", eventId))
        self.store[eventId] = dict(body)
        return self

    def delete(self, calendarId, eventId):
        self.writes.append(("delete", eventId))
        self.store.pop(eventId, None)
        return self

    def execute(self):
        return {"items": list(self.store.values())}


class FakeService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


def test_sync_calendar_dry_run_then_idempotent():
    daily = SimpleNamespace(
        id=7,
        calendar_date=date(2026, 11, 17),
        sleep_start_at=datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc),
        sleep_end_at=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc),
        sleep_time_seconds=27000,
        nap_time_seconds=1800,
        sleep_score=80,
        deep_sleep_seconds=None,
        light_sleep_seconds=None,
        rem_sleep_seconds=None,
        awake_sleep_seconds=None,
    )
    activity = SimpleNamespace(
        id=1,
        source_type="garmin",
        activity_id="123",
        activity_name="晨跑",
        sport_type_raw="RUNNING",
        start_time_gmt=datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc),
        start_time_local=datetime(2026, 11, 17, 8, 30),
        end_time_gmt=datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc),
        distance_meters=10000,
        duration_seconds=5400,
        moving_duration_seconds=5300,
        average_hr=None,
        location_name=None,
        elevation_gain=None,
    )
    db = Mock()
    db.query.side_effect = lambda model: FakeQuery(
        [daily] if model is SleepDaily else [activity]
    )
    user = SimpleNamespace(id=1, timezone="Asia/Shanghai")
    service = FakeService()

    preview = svc.sync_calendar(
        db, user, days=7, dry_run=True, service=service, calendar_id="cal"
    )
    assert preview["created"] == 3
    assert preview["skipped"] == 0
    assert service._events.writes == []

    first = svc.sync_calendar(
        db, user, days=7, dry_run=False, service=service, calendar_id="cal"
    )
    assert first["created"] == 3
    assert len(service._events.store) == 3

    second = svc.sync_calendar(
        db, user, days=7, dry_run=False, service=service, calendar_id="cal"
    )
    assert second["created"] == 0
    assert second["skipped"] == 3
    assert second["deleted"] == 0


def test_sync_calendar_single_date_window():
    daily = SimpleNamespace(
        id=9,
        calendar_date=date(2026, 11, 17),
        sleep_start_at=datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc),
        sleep_end_at=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc),
        sleep_time_seconds=27000,
        nap_time_seconds=0,
        sleep_score=None,
        deep_sleep_seconds=None,
        light_sleep_seconds=None,
        rem_sleep_seconds=None,
        awake_sleep_seconds=None,
    )
    db = Mock()
    db.query.side_effect = lambda model: FakeQuery(
        [daily] if model is SleepDaily else []
    )
    user = SimpleNamespace(id=1, timezone="Asia/Shanghai")
    service = FakeService()

    stats = svc.sync_calendar(
        db,
        user,
        year=2026,
        month=11,
        day=17,
        dry_run=True,
        service=service,
        calendar_id="cal",
    )
    assert stats["window"] == ["2026-11-17", "2026-11-17"]
    assert stats["desired_events"] == 1
    assert stats["created"] == 1
