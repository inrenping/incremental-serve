"""FIT → Suunto JSON SML 转换器。

颂拓私有 API 的「写活动」端点 ``POST /v1/workout`` 接收的是 **SML**（不是 FIT）。
读活动时 ``GET /v1/workouts/{key}/sml`` 返回的 JSON 结构（已在 suuntool 的
``sml_filter.go`` 中逆向确认）即我们这里要造的结构：

{
  "Data": {
    "Samples": [
      {
        "TimeISO8601": "2019-04-18T09:25:29.000+00:00",
        "Source": "suunto-123456789",
        "Attributes": {
          "suunto/sml": {
            "Sample": {
              "GPSAltitude": 94,            # 米
              "Latitude": 0.8892432869,     # 弧度
              "Longitude": 0.1026143732,    # 弧度
              "UTC": "2019-04-18T07:25:29.000+00:00",
              "HR": 120,
              "Cadence": 80,
              "Speed": 3.21,                # m/s
              "Power": 210,                 # W（自行车）
              ...
            }
          }
        }
      }
    ]
  },
  "Summary": { ... }                         # 汇总统计（最佳猜测结构，见下）
}

注意：
- 经纬度是**弧度**（不是度），由 FIT 的「半圆」(semicircles) 换算：rad = semi * π / 2^31。
- ``UTC`` 与 ``TimeISO8601`` 都用 ISO-8601 字符串（带毫秒与 +00:00）。
- ``Summary`` 的字段名未经真实样本逐字校验（目前没有任何真实 GET /sml 样本）。
  代码里通过 ``include_summary`` 暴露开关；若上传因 Summary 被拒，先关掉它再试，
  并调用 ``suunto_service.get_workout_sml`` 拉一个真实样本回来对字段名做校正。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import fitparse

# FIT 时间戳基准：1989-12-31 00:00:00 UTC
_FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)

# FIT sport 枚举 → 颂拓 activityId（最佳猜测，需用真实样本校正）
_FIT_SPORT_TO_SUUNTO = {
    0: 1,   # running
    1: 3,   # cycling
    2: 1,   # transition -> 回退 running
    3: 19,  # fitness_equipment
    4: 4,   # swimming
    6: 32,  # walking
    7: 5,   # winter_sport
    8: 6,   # team_sport
    9: 7,   # water_sport
}
_FIT_SPORT_NAME_TO_SUUNTO = {
    "running": 1,
    "cycling": 3,
    "transition": 1,
    "fitness_equipment": 19,
    "swimming": 4,
    "walking": 32,
    "winter_sport": 5,
    "team_sport": 6,
    "water_sport": 7,
}
DEFAULT_SUUNTO_ACTIVITY_ID = 1


def _semicircles_to_radians(semis: int) -> float:
    """FIT 半圆坐标 → 弧度。2^31 半圆 = 180° = π rad。"""
    return float(semis) * (3.141592653589793 / 2**31)


def _to_utc_iso(dt: datetime) -> str:
    """输出 ``2019-04-18T07:25:29.000+00:00`` 形态。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{dt.microsecond // 1000:03d}+00:00"
    )


def _fit_ts_to_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, int):
        return _FIT_EPOCH + timedelta(seconds=value)
    return None


def _pick(values: dict, *names):
    for n in names:
        if values.get(n) is not None:
            return values[n]
    return None


def fit_bytes_to_sml(
    fit_bytes: bytes,
    device_source: str = "suunto-imported",
    activity_name: str = "",
    activity_id: Optional[int] = None,
    include_summary: bool = True,
) -> dict:
    """把一段 FIT 二进制转成颂拓 JSON SML（``Data.Samples`` + 可选 ``Summary``）。

    Args:
        fit_bytes: 标准 FIT 文件内容。
        device_source: ``Source`` 字段值，形如 ``suunto-<device_id>``。
        activity_name: 活动名称（写入 Summary.Activity）。
        activity_id: 显式指定颂拓 activityId；为 None 时按 FIT sport 推断。
        include_summary: 是否生成 Summary 块（字段名未经真实样本校验）。

    Returns:
        与 GET /v1/workouts/{key}/sml 同构的 dict。
    """
    fitfile = fitparse.FitFile(fit_bytes)

    samples: list[dict] = []
    hr_vals: list[float] = []
    cad_vals: list[float] = []
    spd_vals: list[float] = []
    alt_vals: list[float] = []
    start_dt: Optional[datetime] = None

    for msg in fitfile.get_messages("record"):
        v = msg.get_values()
        dt = _fit_ts_to_dt(v.get("timestamp"))
        if dt is None:
            continue
        if start_dt is None:
            start_dt = dt

        sample: dict = {}
        lat = _pick(v, "position_lat")
        lon = _pick(v, "position_long")
        if lat is not None and lon is not None:
            sample["Latitude"] = _semicircles_to_radians(lat)
            sample["Longitude"] = _semicircles_to_radians(lon)

        alt = _pick(v, "enhanced_altitude", "altitude")
        if alt is not None:
            sample["GPSAltitude"] = float(alt)
            alt_vals.append(float(alt))

        hr = _pick(v, "heart_rate")
        if hr is not None:
            sample["HR"] = int(hr)
            hr_vals.append(float(hr))

        cad = _pick(v, "cadence")
        if cad is not None:
            sample["Cadence"] = int(cad)
            cad_vals.append(float(cad))

        spd = _pick(v, "enhanced_speed", "speed")
        if spd is not None:
            sample["Speed"] = float(spd)
            spd_vals.append(float(spd))

        pwr = _pick(v, "power")
        if pwr is not None:
            sample["Power"] = int(pwr)

        temp = _pick(v, "temperature")
        if temp is not None:
            sample["Temperature"] = float(temp)

        sample["UTC"] = _to_utc_iso(dt)
        if not sample:
            continue
        samples.append(
            {
                "TimeISO8601": sample["UTC"],
                "Source": device_source,
                "Attributes": {"suunto/sml": {"Sample": sample}},
            }
        )

    if not samples:
        raise ValueError("FIT 文件中没有可用 record（GPS/心率样本），无法生成 SML")

    # ---- Summary（最佳猜测结构） ----
    summary = {}
    if include_summary:
        sessions = list(fitfile.get_messages("session"))
        sess = sessions[0].get_values() if sessions else {}

        duration = _pick(sess, "total_timer_time", "total_elapsed_time")
        if duration is None and start_dt:
            duration = (start_dt - _FIT_EPOCH).total_seconds()  # 退化估计
        distance = _pick(sess, "total_distance")
        ascent = _pick(sess, "total_ascent")
        descent = _pick(sess, "total_descent")
        energy = _pick(sess, "total_calories")

        def _agg(vals, fn):
            return round(fn(vals), 2) if vals else None

        summary = {
            "Duration": round(duration, 2) if duration is not None else None,
            "Distance": round(distance, 2) if distance is not None else None,
            "Ascent": round(ascent, 2) if ascent is not None else None,
            "Descent": round(descent, 2) if descent is not None else None,
            "Energy": int(energy) if energy is not None else None,
            "HR": {
                "Avg": _agg(hr_vals, lambda x: sum(x) / len(x)),
                "Min": _agg(hr_vals, min),
                "Max": _agg(hr_vals, max),
            },
            "Speed": {
                "Avg": _agg(spd_vals, lambda x: sum(x) / len(x)),
                "Max": _agg(spd_vals, max),
            },
            "Cadence": {
                "Avg": _agg(cad_vals, lambda x: sum(x) / len(x)),
                "Max": _agg(cad_vals, max),
            },
            "Altitude": {
                "Avg": _agg(alt_vals, lambda x: sum(x) / len(x)),
                "Min": _agg(alt_vals, min),
                "Max": _agg(alt_vals, max),
            },
        }

        if activity_id is None:
            sport = sess.get("sport")
            if isinstance(sport, str):
                activity_id = _FIT_SPORT_NAME_TO_SUUNTO.get(
                    sport.lower(), DEFAULT_SUUNTO_ACTIVITY_ID
                )
            elif isinstance(sport, int):
                activity_id = _FIT_SPORT_TO_SUUNTO.get(
                    sport, DEFAULT_SUUNTO_ACTIVITY_ID
                )
            else:
                activity_id = DEFAULT_SUUNTO_ACTIVITY_ID

        summary["ActivityType"] = int(activity_id)
        summary["Activity"] = activity_name or ""
        summary["DateTime"] = _to_utc_iso(start_dt) if start_dt else None

    result: dict = {"Data": {"Samples": samples}}
    if include_summary:
        result["Summary"] = summary
    return result
