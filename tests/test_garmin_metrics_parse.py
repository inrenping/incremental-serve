"""佳明体能指标：解析与落库逻辑的离线校验。

不依赖网络，也不依赖真实数据库：
1. 解析函数用「构造出的佳明风格响应 + 本地探测存档」喂进去，确认取值正确；
2. upsert 用 Mock Session 验证覆盖式写入与幂等（同一条记录只应有一行）。

真实响应结构参考 scripts/.probe_out/，那个账号没有手表，
mostRecentTrainingStatus 全为 null，所以这里用构造数据补齐有值的情况。
"""

from unittest.mock import MagicMock
from datetime import date

from app.services import garmin_metrics_service as svc


# --------------------------------------------------------------------------
# 1. 训练状态 / 训练负荷 / VO2max
# --------------------------------------------------------------------------

TRAINING_STATUS_OK = {
    "userId": 122080325,
    "mostRecentTrainingStatus": {
        "calendarDate": "2026-10-04",
        "trainingStatus": "PRODUCTIVE",
        "trainingStatusFeedbackPhrase": "TRAINING_STATUS_PRODUCTIVE_FEEDBACK",
        "trainingPaused": False,
        "sinceDate": "2026-09-01",
        "weeklyTrainingLoad": 512.0,
        "dailyTrainingLoadAcute": 703.5,
        "dailyTrainingLoadChronic": 651.2,
        "dailyAcuteChronicWorkloadRatio": 1.08,
        "acwrStatus": "OPTIMAL",
        "acwrPercent": 108,
        "loadTunnelMin": 400.0,
        "loadTunnelMax": 900.0,
        "loadLevelTrend": "INCREASING",
        "fitnessTrend": "IMPROVING",
    },
    "mostRecentVO2Max": {
        "generic": {
            "calendarDate": "2026-10-03",
            "vo2MaxValue": 52,
            "vo2MaxPreciseValue": 52.4,
        },
        "running": {"calendarDate": "2026-10-03", "vo2MaxValue": 54},
        "cycling": {"calendarDate": "2026-09-20", "vo2MaxValue": 48},
    },
}


def test_parse_training_status_full():
    parsed = svc._parse_training_status(TRAINING_STATUS_OK)

    assert parsed["training_status"] == "PRODUCTIVE"
    assert parsed["calendar_date"] == date(2026, 10, 4)
    assert parsed["since_date"] == date(2026, 9, 1)
    assert parsed["weekly_training_load"] == 512.0
    assert parsed["daily_training_load_acute"] == 703.5
    assert parsed["daily_training_load_chronic"] == 651.2
    assert parsed["acute_chronic_workload_ratio"] == 1.08
    assert parsed["acwr_status"] == "OPTIMAL"
    assert parsed["load_tunnel_min"] == 400.0
    assert parsed["load_tunnel_max"] == 900.0
    # VO2max 三层：generic / running / cycling 分开取
    assert parsed["vo2_max_value"] == 52
    assert parsed["vo2_max_precise_value"] == 52.4
    assert parsed["vo2_max_running"] == 54
    assert parsed["vo2_max_cycling"] == 48
    assert parsed["vo2_max_calendar_date"] == date(2026, 10, 3)


def test_parse_training_status_null_payload():
    """探测账号（无手表）的真实返回：整块都是 null，不能报错。"""
    parsed = svc._parse_training_status(
        {
            "userId": 122080325,
            "mostRecentVO2Max": None,
            "mostRecentTrainingLoadBalance": None,
            "mostRecentTrainingStatus": None,
            "heatAltitudeAcclimationDTO": None,
        }
    )
    assert parsed is not None
    assert parsed["training_status"] is None
    assert parsed["vo2_max_value"] is None


def test_parse_training_status_bad_input():
    assert svc._parse_training_status(None) is None
    assert svc._parse_training_status("oops") is None


# --------------------------------------------------------------------------
# 2. 体能年龄（走 maxmet）
# --------------------------------------------------------------------------


def test_parse_fitness_age_from_list():
    parsed = svc._parse_fitness_age(
        [
            {"calendarDate": "2026-10-02", "maxMet": 11.5, "fitnessAge": 31},
            {"calendarDate": "2026-10-04", "maxMet": 12.5, "fitnessAge": 29},
        ]
    )
    assert parsed["calendar_date"] == date(2026, 10, 4)
    assert parsed["fitness_age"] == 29
    assert parsed["max_met"] == 12.5


def test_parse_fitness_age_empty():
    """探测账号实测返回 []。"""
    assert svc._parse_fitness_age([]) is None


def test_parse_fitness_age_without_fitness_age_key():
    """maxmet 里没有 fitnessAge 时，至少要留下 maxMet。"""
    parsed = svc._parse_fitness_age([{"calendarDate": "2026-10-04", "maxMet": 12.5}])
    assert parsed is not None
    assert parsed["max_met"] == 12.5
    assert parsed["fitness_age"] is None


# --------------------------------------------------------------------------
# 3. 个人纪录
# --------------------------------------------------------------------------

PERSONAL_RECORDS_OK = [
    {
        "typeId": 3,
        "activityType": "running",
        "value": 1234.56,
        "activityId": 24583888497,
        "activityName": "早晨跑步",
        "prStartTimeGmt": "2026-10-03T06:33:43.0",
        "activityStartDateTimeInGMT": "2026-10-03T06:33:43.0",
    },
    {
        "typeId": 7,
        "activityType": "running",
        "value": 21100.0,
        "activityId": 24583000000,
        "activityName": "长距离",
        "prStartTimeGmt": "2026-09-20T06:00:00.0",
        "activityStartDateTimeInGMT": "2026-09-20T06:00:00.0",
    },
    {
        "typeId": 1,
        "activityType": "running",
        "value": 250.0,
        "activityId": 24583111111,
        "activityName": "间歇",
        "prStartTimeGmt": "2026-08-01T06:00:00.0",
        "activityStartDateTimeInGMT": "2026-08-01T06:00:00.0",
    },
]


def test_parse_personal_records_units():
    parsed = svc._parse_personal_records(PERSONAL_RECORDS_OK)
    by_type = {item["type_id"]: item for item in parsed}

    assert len(parsed) == 3
    # 5 公里（type 3）单位是秒
    assert by_type[3]["type_key"] == "fastest_5k"
    assert by_type[3]["unit"] == "second"
    assert by_type[3]["value_seconds"] == 1234.56
    assert by_type[3]["value_meters"] is None
    # 最长跑（type 7）单位是米
    assert by_type[7]["type_key"] == "longest_run"
    assert by_type[7]["unit"] == "meter"
    assert by_type[7]["value_meters"] == 21100.0
    assert by_type[7]["value_seconds"] is None
    # 时间字段要能解析成 datetime
    assert by_type[3]["achieved_at"].year == 2026


def test_parse_personal_records_non_running():
    """非跑步项目语义未确认：存原值，unit 留空，type_key 带上项目名。"""
    parsed = svc._parse_personal_records(
        [{"typeId": 3, "activityType": "cycling", "value": 900.0}]
    )
    assert len(parsed) == 1
    assert parsed[0]["activity_type"] == "cycling"
    assert parsed[0]["type_key"] == "cycling_3"
    assert parsed[0]["unit"] is None
    assert parsed[0]["value"] == 900.0


def test_parse_personal_records_bad_input():
    assert svc._parse_personal_records([]) == []
    assert svc._parse_personal_records(None) == []
    # 没有 typeId 的脏数据直接跳过
    assert svc._parse_personal_records([{"value": 1.0}]) == []


# --------------------------------------------------------------------------
# 4. 比赛预测
# --------------------------------------------------------------------------


def test_parse_race_predictions_dto_shape():
    parsed = svc._parse_race_predictions(
        {
            "raceTimePredictionDTOs": [
                {
                    "timePredictorType": "FIVE_K",
                    "predictedTimeInSeconds": 1234,
                    "predictedTimeText": "20:34",
                },
                {
                    "timePredictorType": "MARATHON",
                    "predictedTimeInSeconds": 10800,
                    "predictedTimeText": "3:00:00",
                },
            ]
        }
    )
    by_type = {item["race_type"]: item for item in parsed}
    assert len(parsed) == 2
    assert by_type["FIVE_K"]["predicted_seconds"] == 1234
    # 没给距离时按内置表补
    assert by_type["FIVE_K"]["distance_meters"] == 5000
    assert by_type["MARATHON"]["distance_meters"] == 42195
    assert by_type["MARATHON"]["predicted_time_text"] == "3:00:00"


def test_parse_race_predictions_plain_list():
    parsed = svc._parse_race_predictions(
        [{"raceType": "TEN_K", "predictedTime": 2700, "distanceMeters": 10000}]
    )
    assert len(parsed) == 1
    assert parsed[0]["race_type"] == "TEN_K"
    assert parsed[0]["predicted_seconds"] == 2700


def test_parse_race_predictions_missing_type_skipped():
    """识别不出项目类型的条目不能写库，否则 race_type 会撞主键。"""
    assert svc._parse_race_predictions([{"predictedTimeInSeconds": 100}]) == []


# --------------------------------------------------------------------------
# 5. upsert：覆盖式写入 + 幂等
# --------------------------------------------------------------------------


def _mock_session(existing=None):
    """模拟 Session：query().filter().first() 返回 existing，add 记录新建对象。"""
    db = MagicMock()
    added = []

    def add(obj):
        added.append(obj)

    db.add.side_effect = add
    db.added = added
    chain = MagicMock()
    chain.filter.return_value.first.return_value = existing
    db.query.return_value = chain
    return db


def test_upsert_training_status_reuses_single_row():
    existing = MagicMock()
    db = _mock_session(existing=existing)

    svc._upsert_training_status(
        db, 1, 1, svc._parse_training_status(TRAINING_STATUS_OK), TRAINING_STATUS_OK
    )

    # 已经有一行时不能再新建，保证 UNIQUE(user_id)
    assert db.added == []
    assert existing.training_status == "PRODUCTIVE"
    assert existing.vo2_max_value == 52
    assert existing.raw == TRAINING_STATUS_OK


def test_upsert_training_status_creates_row_when_missing():
    db = _mock_session(existing=None)
    svc._upsert_training_status(
        db, 1, 1, svc._parse_training_status(TRAINING_STATUS_OK), TRAINING_STATUS_OK
    )
    assert len(db.added) == 1


def test_upsert_personal_records_empty_is_noop():
    """拉取为空多半是接口异常，不能把已有纪录删光。"""
    db = _mock_session()
    result = svc._upsert_personal_records(db, 1, 1, [])
    assert result["synced"] is False
    assert result["count"] == 0
    assert "reason" in result
    db.query.assert_not_called()


def test_upsert_race_predictions_empty_is_noop():
    db = _mock_session()
    result = svc._upsert_race_predictions(db, 1, 1, [])
    assert result["synced"] is False
    db.query.assert_not_called()
