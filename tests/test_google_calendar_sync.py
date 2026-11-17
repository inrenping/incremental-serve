"""Google 日历同步的纯函数/离线对账测试（不访问真实 Google API）。"""

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")

from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.sleep_daily import SleepDaily
from app.models.sleep_nap import SleepNap
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

    # 逐条小睡：带具体时段，key 稳定
    nap_row = SimpleNamespace(
        daily_id=7,
        start_at=datetime(2026, 11, 17, 6, 58, 34, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 17, 9, 2, 34, tzinfo=timezone.utc),
        duration_seconds=7440,
        nap_feedback="LATE_TIMING_LONG_DURATION_LOW_NEED",
    )
    detail = svc.build_nap_detail_event(nap_row, daily, _tz())
    assert detail["summary"] == "小睡 2小时04分"
    assert detail["start"]["dateTime"].startswith("2026-11-17T14:58:34+08:00")
    assert detail["end"]["dateTime"].startswith("2026-11-17T17:02:34+08:00")
    assert detail["extendedProperties"]["private"]["inc_kind"] == "nap"
    assert detail["id"] == svc.build_nap_detail_event(nap_row, daily, _tz())["id"]


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

    def order_by(self, *args):
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
    # 查询 BaseConnect.id 返回主连接，SleepNap 返回小睡明细，其余按 model 返回 sleep / activity。
    def query_side_effect(model):
        if model is BaseConnect.id:
            return FakeQuery([(10,)])
        if model is SleepNap:
            return FakeQuery(
                [
                    SimpleNamespace(
                        daily_id=7,
                        start_at=datetime(2026, 11, 17, 6, 58, tzinfo=timezone.utc),
                        end_at=datetime(2026, 11, 17, 7, 28, tzinfo=timezone.utc),
                        duration_seconds=1800,
                        nap_feedback=None,
                    )
                ]
            )
        return FakeQuery([daily] if model is SleepDaily else [activity])

    db.query.side_effect = query_side_effect
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


def test_activities_only_from_master_connect():
    """运动只同步主数据源连接的数据；无主连接时直接返回空。"""
    queried_models = []

    db = Mock()

    def query_side_effect(model):
        queried_models.append(model)
        if model is BaseConnect.id:
            return FakeQuery([(10,)])
        return FakeQuery([SimpleNamespace(id=1)])

    db.query.side_effect = query_side_effect
    result = svc._query_activities(db, 1, date(2026, 11, 1), date(2026, 11, 17))
    assert result == [SimpleNamespace(id=1)]
    assert BaseConnect.id in queried_models

    # 没有主连接时直接返回空，不查运动表
    queried_models.clear()
    db_no_master = Mock()
    db_no_master.query.side_effect = lambda model: (
        queried_models.append(model) or FakeQuery([])
    )
    assert svc._query_activities(
        db_no_master, 1, date(2026, 11, 1), date(2026, 11, 17)
    ) == []
    assert queried_models == [BaseConnect.id]


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
        [(10,)] if model is BaseConnect.id else ([daily] if model is SleepDaily else [])
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


def test_parse_nap_dtos_extracts_per_nap_times():
    from app.services.garmin_service import parse_nap_dtos

    dto = {
        "dailyNapDTOS": [
            {
                "napStartTimestampGMT": "2026-11-14T06:58:34",
                "napEndTimestampGMT": "2026-11-14T09:02:34",
                "napTimeSec": 7440,
                "napStartTimeOffset": 480,
                "napFeedback": "LATE_TIMING_LONG_DURATION_LOW_NEED",
            },
            {
                # 乱序的第二条：解析结果应按开始时间排序
                "napStartTimestampGMT": "2026-11-14T02:00:00",
                "napEndTimestampGMT": "2026-11-14T02:30:00",
                "napTimeSec": 1800,
            },
            {
                # 缺结束时间 → 跳过
                "napStartTimestampGMT": "2026-11-14T03:00:00",
            },
            {
                # 缺 napTimeSec → 按起止时间推算
                "napStartTimestampGMT": "2026-11-14T10:00:00",
                "napEndTimestampGMT": "2026-11-14T10:20:00",
            },
        ]
    }
    rows = parse_nap_dtos(dto)
    assert len(rows) == 3
    assert rows[0]["duration_seconds"] == 1800
    assert rows[1]["duration_seconds"] == 7440
    assert rows[1]["nap_feedback"] == "LATE_TIMING_LONG_DURATION_LOW_NEED"
    assert rows[2]["duration_seconds"] == 1200
    assert rows[0]["start_at"] == datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)

    assert parse_nap_dtos({}) == []
    assert parse_nap_dtos({"dailyNapDTOS": None}) == []


def test_nap_summary_fallback_only_without_details():
    """有逐条明细时只发逐条事件；无明细但有汇总秒数时才用全天占位。"""
    daily = SimpleNamespace(
        id=7,
        calendar_date=date(2026, 11, 17),
        sleep_start_at=datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc),
        sleep_end_at=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc),
        sleep_time_seconds=27000,
        nap_time_seconds=1800,
        sleep_score=None,
        deep_sleep_seconds=None,
        light_sleep_seconds=None,
        rem_sleep_seconds=None,
        awake_sleep_seconds=None,
    )
    nap_row = SimpleNamespace(
        daily_id=7,
        start_at=datetime(2026, 11, 17, 6, 58, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 17, 7, 28, tzinfo=timezone.utc),
        duration_seconds=1800,
        nap_feedback=None,
    )

    with_details = svc.build_desired_events([daily], [], {7: [nap_row]}, _tz())
    kinds = {e["extendedProperties"]["private"]["inc_kind"] for e in with_details.values()}
    assert kinds == {"sleep", "nap"}

    without_details = svc.build_desired_events([daily], [], {}, _tz())
    kinds = {e["extendedProperties"]["private"]["inc_kind"] for e in without_details.values()}
    assert kinds == {"sleep", "nap-summary"}
