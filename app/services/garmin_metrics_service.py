"""佳明体能指标同步：训练状态·负荷、体能年龄、个人纪录、比赛预测。

落库目标就是 20261005 那四张表，全部是「一人一份最新快照」，重复同步即覆盖，不记历史。

关于字段口径（重要）：
    这四个接口都是 Garmin Connect 的私有逆向接口，返回的字段名没有官方文档。
    本模块已用真实账号探测过连通性，但探测账号没有手表健康数据，
    所以「有值时字段长什么样」仍未完全确认。
    因此解析一律走宽松取值 `_pick` / `_deep_pick`（多候选键名 + 嵌套搜索），
    并且每张表都把原始响应塞进 raw 列。等拿到有数据的账号跑一遍，
    再回来把键名收紧即可，不需要改表结构。

已知的两个坑：
    1. 个人纪录端点路径必须带 displayName（socialProfile 里的 UUID），
       传 userProfileId 会 403。
    2. 比赛预测端点 2026-10 实测对无手表数据的账号返回 404，
       取不到时跳过、表里保留上次的值，并在返回里标注 skipped 原因。
"""

import os

os.environ["GARTH_TELEMETRY_ENABLED"] = "false"

import garth  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Any, Optional  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models.base_connect import BaseConnect  # noqa: E402
from app.models.garmin_fitness_age import GarminFitnessAge  # noqa: E402
from app.models.garmin_personal_record import GarminPersonalRecord  # noqa: E402
from app.models.garmin_race_prediction import GarminRacePrediction  # noqa: E402
from app.models.garmin_training_status import GarminTrainingStatus  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import garmin_service  # noqa: E402
from app.utils.logger_utils import log_request  # noqa: E402


# --------------------------------------------------------------------------
# 端点与常量
# --------------------------------------------------------------------------

PATH_TRAINING_STATUS = "/metrics-service/metrics/trainingstatus/aggregated"
PATH_MAX_MET = "/metrics-service/metrics/maxmet/daily"
PATH_RACE_PREDICTIONS = "/metrics-service/metrics/racepredictions"
PATH_PERSONAL_RECORDS = "/personalrecord-service/personalrecord/prs"
PATH_SOCIAL_PROFILE = "/userprofile-service/socialProfile"

# 跑步 PR 的 type_id 语义：1~6 是「最快用时」，7 是「最长距离」
# 其余（骑行、步数、爬升等）佳明也给 type_id，这里先不硬编码，等真实数据补齐
PR_TYPE_MAP = {
    1: ("fastest_1km", "second"),
    2: ("fastest_1mile", "second"),
    3: ("fastest_5k", "second"),
    4: ("fastest_10k", "second"),
    5: ("fastest_half_marathon", "second"),
    6: ("fastest_marathon", "second"),
    7: ("longest_run", "meter"),
}

RACE_DISTANCE_METERS = {
    "FIVE_K": 5000,
    "TEN_K": 10000,
    "HALF_MARATHON": 21097.5,
    "MARATHON": 42195,
}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _today_in_user_tz(user: User) -> str:
    """按用户时区取「今天」，避免用 UTC 取日期在凌晨错一天。"""
    tz_name = getattr(user, "timezone", None) or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y-%m-%d")


def _pick(data: Any, *keys, default=None):
    """从 dict 里按多个候选键名取值，取到第一个非 None 的就返回。"""
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _deep_pick(data: Any, *keys, default=None, max_depth=6):
    """在嵌套结构里按 key 名找第一个非 None 的值。

    用于 VO2max 这种 {generic/running/cycling} 分层、且分层结构未确认的字段。
    只在当前层取不到时才往下钻，优先近的。
    """
    if isinstance(data, dict):
        for key in keys:
            if data.get(key) is not None:
                return data[key]
    if max_depth <= 0:
        return default
    if isinstance(data, dict):
        for value in data.values():
            found = _deep_pick(value, *keys, default=None, max_depth=max_depth - 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _deep_pick(item, *keys, default=None, max_depth=max_depth - 1)
            if found is not None:
                return found
    return default


def _to_date(value: Any):
    """把 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM:SS.0' 解析成 date。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.split("T")[0]
    try:
        parts = text.split("-")
        if len(parts) != 3:
            return None
        from datetime import date as _date

        return _date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _to_datetime(value: Any):
    """把佳明的 epoch 毫秒或 ISO 串解析成 UTC datetime（带时区）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        from datetime import timezone as _tz

        return datetime.fromtimestamp(value / 1000, tz=_tz.utc)
    text = str(value).strip().replace("Z", "")
    if "." in text:
        text = text.split(".")[0]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone as _tz

        parsed = parsed.replace(tzinfo=_tz.utc)
    return parsed


def _prepare_garmin_client(
    connect_id: int, db: Session, current_user: User
) -> BaseConnect:
    """装载该连接的凭证到 garth，并配置好域名。

    与心率 / 睡眠链路同构：先探活，失效先用账号密码重登，再退回 OAuth2 刷新。
    沿用 garth 模块级单例（并发跨用户场景有已知隐患，与既有实现保持一致）。
    """
    config = garmin_service.get_garmin_connect(connect_id, db, current_user)
    if not config:
        raise HTTPException(status_code=404, detail="未找到 Garmin 授权配置")

    if garmin_service.test_garmin_token(config.id, db, current_user):
        base_connect = config
    else:
        try:
            base_connect = garmin_service.refresh_garmin_secret_string(
                config.id, db, current_user
            )
        except HTTPException:
            base_connect = garmin_service.refresh_garmin_access_token(
                config.id, db, current_user
            )

    domain = (
        "garmin.cn"
        if (base_connect.region or "").upper() == "CN"
        else "garmin.com"
    )
    garth.client.configure(domain=domain, ssl_verify=(domain != "garmin.cn"))
    return base_connect


def _fetch(path: str, current_user: User, op_desc: str, params=None, raises=True):
    """带日志地打一个 connectapi 端点；失败按 raises 决定抛异常还是返回 None。"""
    full_url = f"https://connect.{garth.client.domain}{path}"
    try:
        with log_request(
            current_user=current_user,
            req_url=full_url,
            req_method="GET",
            req_params=params,
            log_type="query",
            module_name="garmin",
            op_desc=op_desc,
        ):
            return garth.connectapi(path, params=params) if params else garth.connectapi(path)
    except Exception as e:
        message = f"{op_desc}失败: {str(e)}"
        print(message)
        if raises:
            raise HTTPException(status_code=500, detail=message)
        return None


# --------------------------------------------------------------------------
# 1. 训练状态与训练负荷
# --------------------------------------------------------------------------


def _parse_training_status(data: Any) -> Optional[dict]:
    """从 aggregated 响应里拆出训练状态、负荷与 VO2max。"""
    if not isinstance(data, dict):
        return None

    status = _pick(data, "mostRecentTrainingStatus", default=None)
    if not isinstance(status, dict):
        status = {}

    vo2_block = _pick(data, "mostRecentVO2Max", default=None)
    generic = _pick(vo2_block, "generic", default=None) if isinstance(vo2_block, dict) else None
    running = _pick(vo2_block, "running", default=None) if isinstance(vo2_block, dict) else None
    cycling = _pick(vo2_block, "cycling", default=None) if isinstance(vo2_block, dict) else None

    return {
        "calendar_date": _to_date(
            _pick(status, "calendarDate", default=_deep_pick(data, "calendarDate"))
        ),
        "training_status": _pick(status, "trainingStatus", "status"),
        "training_status_feedback_phrase": _pick(
            status, "trainingStatusFeedbackPhrase", "feedbackPhrase"
        ),
        "training_paused": _pick(status, "trainingPaused"),
        "since_date": _to_date(_pick(status, "sinceDate")),
        "weekly_training_load": _pick(status, "weeklyTrainingLoad"),
        "daily_training_load_acute": _pick(status, "dailyTrainingLoadAcute"),
        "daily_training_load_chronic": _pick(status, "dailyTrainingLoadChronic"),
        "acute_chronic_workload_ratio": _pick(status, "dailyAcuteChronicWorkloadRatio"),
        "acwr_status": _pick(status, "acwrStatus"),
        "acwr_percent": _pick(status, "acwrPercent"),
        "load_tunnel_min": _pick(status, "loadTunnelMin"),
        "load_tunnel_max": _pick(status, "loadTunnelMax"),
        "load_level_trend": _pick(status, "loadLevelTrend"),
        "fitness_trend": _pick(status, "fitnessTrend"),
        "vo2_max_value": _deep_pick(generic or vo2_block, "vo2MaxValue"),
        "vo2_max_precise_value": _deep_pick(generic or vo2_block, "vo2MaxPreciseValue"),
        "vo2_max_calendar_date": _to_date(_deep_pick(generic or vo2_block, "calendarDate")),
        "vo2_max_running": _deep_pick(running, "vo2MaxValue"),
        "vo2_max_cycling": _deep_pick(cycling, "vo2MaxValue"),
    }


def _upsert_training_status(
    db: Session, user_id: int, connect_id: int, parsed: dict, raw: Any
) -> GarminTrainingStatus:
    record = (
        db.query(GarminTrainingStatus)
        .filter(GarminTrainingStatus.user_id == user_id)
        .first()
    )
    if record is None:
        record = GarminTrainingStatus(user_id=user_id)
        db.add(record)

    record.connect_id = connect_id
    for field, value in parsed.items():
        if field == "calendar_date":
            continue
        setattr(record, field, value)
    record.calendar_date = parsed.get("calendar_date")
    record.raw = raw
    record.synced_at = datetime.utcnow()
    return record


# --------------------------------------------------------------------------
# 2. 体能年龄
# --------------------------------------------------------------------------


def _parse_fitness_age(data: Any) -> Optional[dict]:
    """maxmet 返回数组，取最新一条。"""
    if isinstance(data, dict):
        data = _pick(data, "maxMetrics", "data", default=data)
        if not isinstance(data, list):
            data = [data]
    if not isinstance(data, list) or not data:
        return None

    item = None
    for entry in data:
        if isinstance(entry, dict) and _pick(entry, "fitnessAge", "vo2MaxValue") is not None:
            item = entry
    if item is None:
        item = data[-1] if isinstance(data[-1], dict) else None
    if not isinstance(item, dict):
        return None

    return {
        "calendar_date": _to_date(_pick(item, "calendarDate")),
        "fitness_age": _pick(item, "fitnessAge"),
        "vo2_max_value": _pick(item, "vo2MaxValue", "vo2Max"),
        "max_met": _pick(item, "maxMet", "maxMetValue"),
    }


def _upsert_fitness_age(
    db: Session, user_id: int, connect_id: int, parsed: dict, raw: Any
) -> GarminFitnessAge:
    record = (
        db.query(GarminFitnessAge).filter(GarminFitnessAge.user_id == user_id).first()
    )
    if record is None:
        record = GarminFitnessAge(user_id=user_id)
        db.add(record)

    record.connect_id = connect_id
    record.calendar_date = parsed.get("calendar_date")
    record.fitness_age = parsed.get("fitness_age")
    record.vo2_max_value = parsed.get("vo2_max_value")
    record.max_met = parsed.get("max_met")
    record.raw = raw
    record.synced_at = datetime.utcnow()
    return record


# --------------------------------------------------------------------------
# 3. 个人纪录
# --------------------------------------------------------------------------


def _parse_personal_records(data: Any) -> list:
    if not isinstance(data, list):
        return []

    parsed = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        type_id = _pick(entry, "typeId", "typeID")
        if type_id is None:
            continue
        activity_type = _pick(entry, "activityType")
        type_key, unit = PR_TYPE_MAP.get(
            int(type_id), (f"type_{type_id}", None)
        )
        if activity_type and activity_type != "running":
            # 非跑步项目的语义尚未确认，unit 留空，只存原值与 raw
            type_key = f"{activity_type}_{type_id}"
            unit = None

        value = _pick(entry, "value")
        parsed.append(
            {
                "type_id": int(type_id),
                "type_key": type_key,
                "activity_type": activity_type,
                "unit": unit,
                "value": value,
                "value_seconds": value if unit == "second" else None,
                "value_meters": value if unit == "meter" else None,
                "activity_id": _pick(entry, "activityId"),
                "activity_name": _pick(entry, "activityName"),
                "achieved_at": _to_datetime(_pick(entry, "prStartTimeGmt")),
                "activity_start_at": _to_datetime(
                    _pick(entry, "activityStartDateTimeInGMT")
                ),
                "raw": entry,
            }
        )
    return parsed


def _upsert_personal_records(
    db: Session, user_id: int, connect_id: int, records: list
) -> dict:
    """覆盖式写入：每个 (type_id, activity_type) 只留最新一条。

    上游删掉的 PR 也会同步删掉本地的，保证和佳明一致；
    但如果本次拉取返回空（多半是接口异常而不是真的没纪录），不动本地数据。
    """
    if not records:
        return {"synced": False, "count": 0, "reason": "接口未返回个人纪录"}

    now = datetime.utcnow()
    seen = set()
    for item in records:
        activity_type = item["activity_type"]
        record = (
            db.query(GarminPersonalRecord)
            .filter(
                GarminPersonalRecord.user_id == user_id,
                GarminPersonalRecord.type_id == item["type_id"],
                GarminPersonalRecord.activity_type == activity_type,
            )
            .first()
        )
        if record is None:
            record = GarminPersonalRecord(
                user_id=user_id, type_id=item["type_id"], activity_type=activity_type
            )
            db.add(record)

        record.connect_id = connect_id
        record.type_key = item["type_key"]
        record.unit = item["unit"]
        record.value = item["value"]
        record.value_seconds = item["value_seconds"]
        record.value_meters = item["value_meters"]
        record.activity_id = item["activity_id"]
        record.activity_name = item["activity_name"]
        record.achieved_at = item["achieved_at"]
        record.activity_start_at = item["activity_start_at"]
        record.raw = item["raw"]
        record.synced_at = now
        seen.add((item["type_id"], activity_type or ""))

    # 清掉上游已经不存在的纪录
    existing = (
        db.query(GarminPersonalRecord)
        .filter(GarminPersonalRecord.user_id == user_id)
        .all()
    )
    removed = 0
    for record in existing:
        key = (record.type_id, record.activity_type or "")
        if key not in seen:
            db.delete(record)
            removed += 1

    return {"synced": True, "count": len(records), "removed": removed}


# --------------------------------------------------------------------------
# 4. 比赛成绩预测
# --------------------------------------------------------------------------


def _parse_race_predictions(data: Any) -> list:
    """比赛预测的返回结构未确认，做尽力解析，识别不了就返回空。"""
    if isinstance(data, dict):
        for key in ("raceTimePredictionDTOs", "predictions", "data", "racePredictions"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []

    parsed = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        raw_type = _pick(
            entry, "timePredictorType", "raceType", "type", "predictorType"
        )
        race_type = str(raw_type).upper() if raw_type else None
        if race_type and race_type not in RACE_DISTANCE_METERS:
            # 未知项目也存，只是没有距离
            pass
        seconds = _pick(
            entry, "predictedTimeInSeconds", "predictedTime", "seconds", "value"
        )
        if race_type is None:
            continue
        parsed.append(
            {
                "race_type": race_type,
                "distance_meters": _pick(
                    entry, "distanceMeters", "distance", default=RACE_DISTANCE_METERS.get(race_type)
                ),
                "predicted_seconds": seconds,
                "predicted_time_text": _pick(entry, "predictedTimeText", "text"),
                "raw": entry,
            }
        )
    return parsed


def _upsert_race_predictions(
    db: Session, user_id: int, connect_id: int, records: list
) -> dict:
    if not records:
        return {"synced": False, "count": 0, "reason": "接口未返回比赛预测"}

    now = datetime.utcnow()
    for item in records:
        record = (
            db.query(GarminRacePrediction)
            .filter(
                GarminRacePrediction.user_id == user_id,
                GarminRacePrediction.race_type == item["race_type"],
            )
            .first()
        )
        if record is None:
            record = GarminRacePrediction(
                user_id=user_id, race_type=item["race_type"]
            )
            db.add(record)
        record.connect_id = connect_id
        record.distance_meters = item["distance_meters"]
        record.predicted_seconds = item["predicted_seconds"]
        record.predicted_time_text = item["predicted_time_text"]
        record.raw = item["raw"]
        record.synced_at = now

    return {"synced": True, "count": len(records)}


# --------------------------------------------------------------------------
# 对外主入口
# --------------------------------------------------------------------------


def sync_garmin_fitness_metrics(
    connect_id: int,
    db: Session,
    current_user: User,
    date: Optional[str] = None,
) -> dict:
    """同步单个连接的四项体能指标。

    :param connect_id: t_base_connect 的 id（佳明连接）
    :param date: 数据日期 YYYY-MM-DD，默认按用户时区取今天
    :return: 四项的同步结果汇总，单项失败不影响其他项
    """
    base_connect = _prepare_garmin_client(connect_id, db, current_user)
    if date is None:
        date = _today_in_user_tz(current_user)

    result: dict = {
        "connect_id": base_connect.id,
        "user_id": current_user.id,
        "region": base_connect.region,
        "date": date,
    }

    # 1. 训练状态与负荷
    try:
        raw = _fetch(
            f"{PATH_TRAINING_STATUS}/{date}",
            current_user,
            f"获取 Garmin {date} 训练状态与负荷",
        )
        parsed = _parse_training_status(raw)
        if parsed is None:
            result["training_status"] = {"synced": False, "reason": "接口返回为空或格式异常"}
        else:
            _upsert_training_status(db, current_user.id, base_connect.id, parsed, raw)
            result["training_status"] = {"synced": True, "data": parsed}
    except Exception as e:  # noqa: BLE001 - 单项失败不能拖垮整次同步
        result["training_status"] = {"synced": False, "reason": str(e)[:200]}

    # 2. 体能年龄
    try:
        raw = _fetch(
            f"{PATH_MAX_MET}/{date}/{date}",
            current_user,
            f"获取 Garmin {date} 体能年龄",
        )
        parsed = _parse_fitness_age(raw)
        if parsed is None:
            result["fitness_age"] = {"synced": False, "reason": "接口返回为空或格式异常"}
        else:
            _upsert_fitness_age(db, current_user.id, base_connect.id, parsed, raw)
            result["fitness_age"] = {"synced": True, "data": parsed}
    except Exception as e:  # noqa: BLE001
        result["fitness_age"] = {"synced": False, "reason": str(e)[:200]}

    # 3. 个人纪录（路径必须带 displayName，传 userProfileId 会 403）
    try:
        profile = _fetch(
            PATH_SOCIAL_PROFILE, current_user, "获取 Garmin 社交资料(displayName)"
        )
        display_name = _pick(profile, "displayName") if isinstance(profile, dict) else None
        if not display_name:
            result["personal_records"] = {
                "synced": False,
                "reason": "取不到 displayName，无法拼个人纪录接口路径",
            }
        else:
            raw = _fetch(
                f"{PATH_PERSONAL_RECORDS}/{display_name}",
                current_user,
                "获取 Garmin 个人纪录",
            )
            records = _parse_personal_records(raw)
            result["personal_records"] = _upsert_personal_records(
                db, current_user.id, base_connect.id, records
            )
    except Exception as e:  # noqa: BLE001
        result["personal_records"] = {"synced": False, "reason": str(e)[:200]}

    # 4. 比赛成绩预测（404 属正常情况，只跳过不报错）
    try:
        raw = _fetch(
            PATH_RACE_PREDICTIONS,
            current_user,
            f"获取 Garmin {date} 比赛成绩预测",
            params={"calendarDate": date},
            raises=True,
        )
        records = _parse_race_predictions(raw)
        result["race_predictions"] = _upsert_race_predictions(
            db, current_user.id, base_connect.id, records
        )
    except Exception as e:  # noqa: BLE001
        # 404 = 端点不可用或无手表数据；表里保留上次的值
        result["race_predictions"] = {"synced": False, "reason": str(e)[:200]}

    db.commit()
    return result


def find_master_garmin_connects(db: Session) -> list:
    """找出所有「主账号是佳明」的连接：master=True 且 source_type=garmin 且激活。"""
    return (
        db.query(BaseConnect)
        .filter(
            BaseConnect.master == True,  # noqa: E712 - SQLAlchemy 需要 == True
            BaseConnect.source_type == "garmin",
            BaseConnect.is_active == True,  # noqa: E712
        )
        .all()
    )


def sync_fitness_metrics_for_all_masters(
    db: Session, date: Optional[str] = None
) -> dict:
    """批量同步：所有主账号为佳明的用户各跑一遍。

    供 GitHub Actions 定时任务调用。单个用户失败只记进 errors，不影响其他人。
    """
    connects = find_master_garmin_connects(db)
    summary = {
        "total": len(connects),
        "synced": 0,
        "failed": 0,
        "details": [],
        "errors": [],
    }

    for connect in connects:
        user = db.query(User).filter(User.id == connect.user_id).first()
        if user is None:
            summary["failed"] += 1
            summary["errors"].append(
                {"connect_id": connect.id, "reason": "用户不存在"}
            )
            continue
        try:
            result = sync_garmin_fitness_metrics(
                connect_id=connect.id, db=db, current_user=user, date=date
            )
            summary["synced"] += 1
            summary["details"].append(result)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            summary["failed"] += 1
            summary["errors"].append(
                {"connect_id": connect.id, "user_id": connect.user_id, "reason": str(e)[:300]}
            )

    return summary
