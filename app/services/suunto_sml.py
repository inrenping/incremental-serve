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
import xml.etree.ElementTree as ET

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
    """把一段 FIT 二进制转成颂拓 **JSON** SML（``Data.Samples`` + 可选 ``Summary``）。

    ⚠️ **上传不要用这个**。``POST /v1/workout`` 的 ``filePart`` 只收 legacy SML
    **XML**，发 JSON 会让服务端反序列化失败并返回 500。上传请用
    :func:`fit_bytes_to_sml_xml`。本函数保留用于对齐/调试读接口
    （``GET /v1/workouts/{key}/sml`` 返回的正是这个 JSON 形态）。

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


# ---------------------------------------------------------------------------
# legacy SML **XML**（上传用）
# ---------------------------------------------------------------------------
#
# 为什么需要 XML：
#   ``GET /v1/workouts/{key}/sml``（读）返回的是 JSON，但 ``POST /v1/workout``（写）
#   的 ``filePart`` 要的是 **legacy SML XML**（suuntool 的 ``workouts upload
#   --sml`` 帮助原文明确写"the SML XML"）。发JSON 会让服务端反序列化失败 → 500。
#
# 结构与字段顺序基准（两个独立来源互相印证）：
#   - ``gkfabs/polar`` 的 ``polar_training2sml``（Ruby/Nokogiri，产出的 SML 被颂拓 App 接受）
#   - ``mihaildemidoff/suunto-sml-model``（JAXB 模型，``@XmlElement`` 声明顺序
#     即 XSD ``sequence`` 的权威顺序 —— 顺序错了服务端会解析失败）
#
# 三个容易踩的单位坑（都与 polar 实现一致，且被真实 SML 样本印证）：
#   - ``HR`` / ``Cadence`` 单位是 **Hz**，即 bpm / 60（真实 SML：
#     ``Header.HR.Avg * 60`` 才是 bpm）
#   - ``Energy`` 单位是 **焦耳**（polar 用 ``kcal2joules``）
#   - ``Latitude`` / ``Longitude`` 单位是 **弧度**（polar 用 ``degree2radian``）

_SML_NS = "http://www.suunto.com/schemas/sml"
_SDK_VERSION = "2.4.89"

# SML 用的是**默认命名空间**（xmlns="..."），不是带前缀的 ns0。
# 服务端的解析器按元素名匹配，带前缀会解析不到，所以这里显式注册空前缀。
ET.register_namespace("", _SML_NS)


def _fmt_num(value, digits: int = 6) -> Optional[str]:
    """数字 → 字符串；None/非数返回 None（该元素直接不写）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    s = f"{round(f, digits):.{digits}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _sml_datetime(dt: datetime) -> str:
    """SML 时间格式 ``yyyy-MM-ddTHH:mm:ss``（JAXB SmlDateAdapter 的 pattern）。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _sub(parent, tag: str, text) -> None:
    """在 ``parent`` 下追加一个 ``<tag>text</tag>``；text 为 None 时跳过。"""
    if text is None:
        return
    ET.SubElement(parent, f"{{{_SML_NS}}}{tag}").text = str(text)


def fit_bytes_to_sml_xml(
    fit_bytes: bytes,
    device_source: str = "suunto-imported",
    device_name: str = "Suunto",
    activity_name: str = "",
    activity_id: Optional[int] = None,
) -> bytes:
    """把 FIT 转成 legacy SML **XML**（上传到 ``POST /v1/workout`` 用）。

    Args:
        fit_bytes: 标准 FIT 文件内容。
        device_source: 设备标识，写进 ``<Device><SerialNumber>``。
        device_name: 设备名，写进 ``<Device><Name>``。
        activity_name: 活动名称，写进 ``<Header><Activity>``。
        activity_id: 颂拓 activityId；为 None 时按 FIT sport 推断。

    Returns:
        UTF-8 编码的 SML XML 字节。
    """
    fitfile = fitparse.FitFile(fit_bytes)

    records: list[dict] = []
    for msg in fitfile.get_messages("record"):
        v = msg.get_values()
        dt = _fit_ts_to_dt(v.get("timestamp"))
        if dt is None:
            continue
        rec: dict = {"dt": dt}
        lat = _pick(v, "position_lat")
        lon = _pick(v, "position_long")
        if lat is not None and lon is not None:
            # FIT 存半圆，SML 存弧度
            rec["lat"] = _semicircles_to_radians(lat)
            rec["lon"] = _semicircles_to_radians(lon)
        alt = _pick(v, "enhanced_altitude", "altitude")
        if alt is not None:
            rec["alt"] = float(alt)
        hr = _pick(v, "heart_rate")
        if hr is not None:
            rec["hr"] = float(hr)
        cad = _pick(v, "cadence")
        if cad is not None:
            rec["cad"] = float(cad)
        spd = _pick(v, "enhanced_speed", "speed")
        if spd is not None:
            rec["spd"] = float(spd)
        temp = _pick(v, "temperature")
        if temp is not None:
            rec["temp"] = float(temp)
        pwr = _pick(v, "power")
        if pwr is not None:
            rec["pwr"] = float(pwr)
        dist = _pick(v, "distance")
        if dist is not None:
            rec["dist"] = float(dist)
        records.append(rec)

    if not records:
        raise ValueError("FIT 文件中没有可用 record（GPS/心率样本），无法生成 SML")

    start_dt = records[0]["dt"]
    # 时间轴：相对起点的秒数（Sample/Time 语义）
    times = [(r["dt"] - start_dt).total_seconds() for r in records]

    sessions = list(fitfile.get_messages("session"))
    sess = sessions[0].get_values() if sessions else {}

    def _sess(*names):
        return _pick(sess, *names)

    duration = _sess("total_timer_time", "total_elapsed_time")
    if duration is None:
        duration = times[-1] if times else 0.0
    distance = _sess("total_distance")
    ascent = _sess("total_ascent")
    descent = _sess("total_descent")
    energy_kcal = _sess("total_calories")

    def _vals(key):
        return [r[key] for r in records if key in r]

    def _avg(v):
        return sum(v) / len(v) if v else None

    def _max_at(v):
        return (max(range(len(v)), key=lambda i: v[i]), max(v)) if v else (0, None)

    hr_all, cad_all, spd_all, alt_all = (
        _vals("hr"), _vals("cad"), _vals("spd"), _vals("alt"))

    if activity_id is None:
        sport = sess.get("sport")
        if isinstance(sport, str):
            activity_id = _FIT_SPORT_NAME_TO_SUUNTO.get(
                sport.lower(), DEFAULT_SUUNTO_ACTIVITY_ID)
        elif isinstance(sport, int):
            activity_id = _FIT_SPORT_TO_SUUNTO.get(sport, DEFAULT_SUUNTO_ACTIVITY_ID)
        else:
            activity_id = DEFAULT_SUUNTO_ACTIVITY_ID

    # ---- 组装 XML ----
    sml = ET.Element(f"{{{_SML_NS}}}sml", {
        "SdkVersion": _SDK_VERSION,
        "Modified": _sml_datetime(datetime.now(timezone.utc)),
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    })
    log = ET.SubElement(sml, f"{{{_SML_NS}}}DeviceLog")

    # ---- Header（顺序严格按 XSD） ----
    hdr = ET.SubElement(log, f"{{{_SML_NS}}}Header")
    _sub(hdr, "Duration", _fmt_num(duration, 3))
    _sub(hdr, "Ascent", _fmt_num(ascent, 2))
    _sub(hdr, "Descent", _fmt_num(descent, 2))

    if spd_all:
        spd_max_i, spd_max = _max_at(spd_all)
        spd = ET.SubElement(hdr, f"{{{_SML_NS}}}Speed")
        _sub(spd, "Avg", _fmt_num(_avg(spd_all)))
        _sub(spd, "Max", _fmt_num(spd_max))
        _sub(spd, "MaxTime", _fmt_num(times[min(spd_max_i, len(times) - 1)], 2))

    if cad_all:
        cad_max_i, cad_max = _max_at(cad_all)
        # SML 里 Cadence 单位是 Hz
        cad = ET.SubElement(hdr, f"{{{_SML_NS}}}Cadence")
        _sub(cad, "Avg", _fmt_num(_avg(cad_all) / 60.0))
        _sub(cad, "Max", _fmt_num(cad_max / 60.0))
        _sub(cad, "MaxTime", _fmt_num(times[min(cad_max_i, len(times) - 1)], 2))

    if alt_all:
        alt_min_i = min(range(len(alt_all)), key=lambda i: alt_all[i])
        alt_max_i, alt_max = _max_at(alt_all)
        alt = ET.SubElement(hdr, f"{{{_SML_NS}}}Altitude")
        _sub(alt, "Max", _fmt_num(alt_max, 2))
        _sub(alt, "Min", _fmt_num(min(alt_all), 2))
        _sub(alt, "MaxTime", _fmt_num(times[min(alt_max_i, len(times) - 1)], 2))
        _sub(alt, "MinTime", _fmt_num(times[min(alt_min_i, len(times) - 1)], 2))

    if hr_all:
        hr_min_i = min(range(len(hr_all)), key=lambda i: hr_all[i])
        hr_max_i, hr_max = _max_at(hr_all)
        # SML 里 HR 单位是 Hz（bpm / 60）
        hr = ET.SubElement(hdr, f"{{{_SML_NS}}}HR")
        _sub(hr, "Avg", _fmt_num(_avg(hr_all) / 60.0))
        _sub(hr, "Max", _fmt_num(hr_max / 60.0))
        _sub(hr, "Min", _fmt_num(min(hr_all) / 60.0))
        _sub(hr, "MaxTime", _fmt_num(times[min(hr_max_i, len(times) - 1)], 2))
        _sub(hr, "MinTime", _fmt_num(times[min(hr_min_i, len(times) - 1)], 2))

    _sub(hdr, "ActivityType", int(activity_id))
    _sub(hdr, "Activity", activity_name or "")
    _sub(hdr, "Distance", _fmt_num(distance, 2))
    _sub(hdr, "LogItemCount", len(records))
    # Energy 单位是焦耳（FIT 给的是 kcal）
    if energy_kcal is not None:
        _sub(hdr, "Energy", _fmt_num(float(energy_kcal) * 4184.0, 1))

    gps_idx = next((i for i, r in enumerate(records) if "lat" in r), None)
    if gps_idx is not None:
        _sub(hdr, "TimeToFirstFix", _fmt_num(times[gps_idx], 2))
    _sub(hdr, "BatteryChargeAtStart", "1")
    _sub(hdr, "BatteryCharge", "0")
    _sub(hdr, "DistanceBeforeCalibrationChange", "0")
    _sub(hdr, "DateTime", _sml_datetime(start_dt))

    # ---- Device ----
    dev = ET.SubElement(log, f"{{{_SML_NS}}}Device")
    _sub(dev, "Name", device_name)
    _sub(dev, "SerialNumber", device_source)

    # ---- Samples ----
    # 子元素顺序严格按 JAXB 模型（= XSD sequence 顺序）：
    #   VerticalSpeed, Cadence, HR, EnergyConsumption, Temperature,
    #   SeaLevelPressure, Time, SampleType, Altitude, Distance, Speed,
    #   WristCadence, WristAccSpeed, BikePodSpeed, UTC,
    #   Latitude, Longitude, EHPE, NumberOfSatellites,
    #   GPSAltitude, GPSHeading, GPSSpeed, GpsHDOP, NavType...
    samples = ET.SubElement(log, f"{{{_SML_NS}}}Samples")
    for i, rec in enumerate(records):
        s = ET.SubElement(samples, f"{{{_SML_NS}}}Sample")
        if "cad" in rec:
            _sub(s, "Cadence", _fmt_num(rec["cad"] / 60.0))
        if "hr" in rec:
            _sub(s, "HR", _fmt_num(rec["hr"] / 60.0))
        if "temp" in rec:
            _sub(s, "Temperature", _fmt_num(rec["temp"], 2))
        _sub(s, "Time", _fmt_num(times[i], 2))
        # 有 GPS 的样本标 gps-base（gps 定位基准），其余标 periodic
        _sub(s, "SampleType", "gps-base" if "lat" in rec else "periodic")
        if "alt" in rec:
            _sub(s, "Altitude", _fmt_num(rec["alt"], 2))
        if "dist" in rec:
            _sub(s, "Distance", _fmt_num(rec["dist"], 2))
        if "spd" in rec:
            _sub(s, "Speed", _fmt_num(rec["spd"]))
        _sub(s, "UTC", _sml_datetime(rec["dt"]))
        if "lat" in rec:
            _sub(s, "Latitude", _fmt_num(rec["lat"]))
            _sub(s, "Longitude", _fmt_num(rec["lon"]))
        if "alt" in rec:
            _sub(s, "GPSAltitude", _fmt_num(rec["alt"], 2))
        if "spd" in rec:
            _sub(s, "GPSSpeed", _fmt_num(rec["spd"]))
    #注：FIT 的``power`` 暂不写入 —— SML 里功率不是 Sample 的直接子元素，
    #   而是 ``Sample/AppsData/AppData(Value)``，需要先确定 AppNumber，
    #   贸然加未知元素反而可能让服务端解析失败。等上传打通后再补。

    _indent(sml)
    return ET.tostring(sml, encoding="UTF-8", xml_declaration=True)


def _indent(elem, level: int = 0) -> None:
    """就地给 XML 加缩进（ET.indent，Python 3.9+ 才有，这里手写以兼容）。"""
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
            if not (child.tail or "").strip():
                child.tail = pad + "  "
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad
