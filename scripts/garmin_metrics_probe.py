#!/usr/bin/env python3
"""探测 garth 能取到的 Garmin 健康 / 训练指标。

为什么要有这个脚本：
    Garmin Connect 的接口是逆向出来的私有接口，同一个端点在国际区（garmin.com）
    和中国区（garmin.cn）的行为并不一致，而且会随手表型号、账号权限返回不同字段。
    光看文档不够，必须拿真实 token 打一遍才知道哪些能落地。

覆盖范围（除已实现的跑量 / 睡眠 / 心率外）：
    步数、训练负荷与训练状态、训练准备度、HRV、VO2max 与体能年龄、耐力分、爬坡分、
    比赛成绩预测、乳酸阈、压力与身体电量、呼吸、血氧、体重体脂、个人纪录、设备。

用法：
    # 方式一：直接喂 garth token（t_base_connect.secret_string 里那串 base64）
    GARTH_TOKEN=<base64> python scripts/garmin_metrics_probe.py
    GARTH_TOKEN=<base64> python scripts/garmin_metrics_probe.py --domain garmin.cn

    # 方式二：从数据库里取（读 .env 的 DATABASE_URL，查 t_base_connect）
    python scripts/garmin_metrics_probe.py --from-db
    python scripts/garmin_metrics_probe.py --from-db --connect-id 12

    # 常用参数
    --date 2026-10-04          探测基准日，默认昨天
    --days 7                   区间类端点的回看天数
    --only 步数,训练负荷        只跑标签里含这些关键字的探测项
    --dump                     把每个端点的原始 JSON 存到 scripts/.probe_out/

退出码：0 = 至少一项成功；1 = 全部失败；2 = 拿不到 token 或网络不通。
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DUMP_DIR = HERE / ".probe_out"


# --------------------------------------------------------------------------
# token 来源
# --------------------------------------------------------------------------


def _read_env_file() -> dict:
    """极简 .env 解析，避免为了一个变量去装 python-dotenv。"""
    env = {}
    for name in (".env", ".env.production"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def token_from_db(connect_id: int | None) -> tuple[str, str]:
    """从 t_base_connect 取一条 Garmin 连接，返回 (token, domain)。"""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("缺少 sqlalchemy，无法用 --from-db；请改用 GARTH_TOKEN 环境变量。")
        sys.exit(2)

    file_env = _read_env_file()
    dsn = os.environ.get("DATABASE_URL") or file_env.get("DATABASE_URL")
    if not dsn:
        print("未找到 DATABASE_URL（环境变量或 .env 里都没有）。")
        sys.exit(2)

    engine = create_engine(dsn, pool_pre_ping=True)
    with engine.connect() as conn:
        if connect_id:
            row = conn.execute(
                text(
                    "SELECT id, account, region, secret_string "
                    "FROM t_base_connect WHERE id = :cid AND source_type = 'garmin'"
                ),
                {"cid": connect_id},
            ).mappings().first()
        else:
            row = conn.execute(
                text(
                    "SELECT id, account, region, secret_string "
                    "FROM t_base_connect WHERE source_type = 'garmin' "
                    "AND secret_string IS NOT NULL AND secret_string <> '' "
                    "ORDER BY id DESC LIMIT 1"
                )
            ).mappings().first()

    if not row:
        print("t_base_connect 里没有可用的 Garmin 连接。")
        sys.exit(2)

    region = (row["region"] or "").upper()
    domain = "garmin.cn" if region == "CN" else "garmin.com"
    print(f"使用数据库连接 id={row['id']} 账号={row['account']} 区域={region or '国际'}")
    return row["secret_string"], domain


def build_client(token: str, domain: str, timeout: int = 20, insecure: bool = False):
    """构造独立 garth 客户端。

    刻意不用模块级 garth.client 单例——那个单例在不同区域的连接之间会串号。
    """
    from garth.http import Client

    client = Client()
    client.loads(token)
    # 中国区证书链在部分环境校验不过，沿用服务端的取舍
    client.configure(
        domain=domain,
        ssl_verify=not (insecure or domain == "garmin.cn"),
        timeout=timeout,
        retries=1,
    )
    return client


# --------------------------------------------------------------------------
# 探测项
# --------------------------------------------------------------------------


def dig(obj, *path, default=None):
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int) and len(cur) > key:
            cur = cur[key]
        else:
            return default
    return cur


def find_keys(obj, names, depth=0, max_depth=6):
    """在嵌套结构里按 key 名找值，端点结构不确定时用来兜底。"""
    if depth > max_depth:
        return {}
    found = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in names and key not in found:
                found[key] = value
        for value in obj.values():
            found.update(find_keys(value, names, depth + 1, max_depth))
    elif isinstance(obj, list):
        for item in obj:
            found.update(find_keys(item, names, depth + 1, max_depth))
    return found


def fmt_value(value):
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return text[:60] + ("…" if len(text) > 60 else "")
    return str(value)


def summarize(data, keys, head=1):
    """统一摘要：list 先看首项，再按 key 名全局搜索。"""
    if data is None:
        return "空响应"
    if isinstance(data, list):
        if not data:
            return "空数组"
        parts = [f"{len(data)} 条"]
        first = data[0] if head else None
        if isinstance(first, dict):
            picked = {k: first[k] for k in keys if k in first}
            parts.append(
                "  ".join(f"{k}={fmt_value(v)}" for k, v in picked.items())
            )
        return " ".join(p for p in parts if p)

    if isinstance(data, dict):
        picked = {k: data[k] for k in keys if k in data}
        if not picked:
            picked = find_keys(data, set(keys))
        if not picked:
            return f"返回 dict，字段={list(data)[:6]}"
        return "  ".join(f"{k}={fmt_value(v)}" for k, v in picked.items())

    return fmt_value(data)


def build_probes(day: str, start: str, end: str):
    """返回 [(标签, 说明, path, params, 关注字段)]"""
    return [
        (
            "步数·日汇总",
            "当天步数、距离、卡路里、静息心率、压力、身体电量、楼层、血氧、呼吸一次拿全",
            "/usersummary-service/usersummary/daily",
            {"calendarDate": day},
            [
                "totalSteps",
                "totalDistanceMeters",
                "activeKilocalories",
                "restingHeartRate",
                "averageStressLevel",
                "bodyBatteryHighestValue",
                "floorsAscended",
                "averageSpo2",
            ],
        ),
        (
            "步数·区间",
            f"{start} ~ {end} 每天的步数与步数目标，做周报/月报不用逐日请求",
            f"/usersummary-service/stats/steps/daily/{start}/{end}",
            None,
            ["calendarDate", "totalSteps", "stepGoal", "totalDistance"],
        ),
        (
            "训练状态·aggregated",
            "训练负荷与状态主接口：急/慢性负荷、ACWR、负荷区间、VO2max",
            f"/metrics-service/metrics/trainingstatus/aggregated/{day}",
            None,
            [
                "weeklyTrainingLoad",
                "dailyTrainingLoadAcute",
                "dailyTrainingLoadChronic",
                "dailyAcuteChronicWorkloadRatio",
                "acwrStatus",
                "trainingStatusFeedbackPhrase",
                "vo2MaxValue",
            ],
        ),
        (
            "训练状态·mobile-gateway",
            "garth stats.TrainingStatus 走的就是它；国际区可用，中国区常 404",
            f"/mobile-gateway/usersummary/trainingstatus/latest/{day}",
            None,
            [
                "weeklyTrainingLoad",
                "dailyTrainingLoadAcute",
                "dailyTrainingLoadChronic",
                "dailyAcuteChronicWorkloadRatio",
                "loadTunnelMin",
                "loadTunnelMax",
                "fitnessTrend",
            ],
        ),
        (
            "训练负荷·fitnessstats",
            f"{start} ~ {end} 每次活动的训练效果与自适应教练状态",
            "/fitnessstats-service/activity/all",
            {
                "startDate": start,
                "endDate": end,
                "standardizedUnits": "true",
                "metric": [
                    "activityType",
                    "workoutType",
                    "aerobicTrainingEffect",
                    "adaptiveCoachingWorkoutStatus",
                    "workoutGroupEnumerator",
                ],
            },
            ["activityType", "aerobicTrainingEffect", "workoutType"],
        ),
        (
            "训练准备度",
            "今晨准备度分数，以及睡眠/恢复/ACWR/压力/HRV 各因子占比与反馈",
            f"/metrics-service/metrics/trainingreadiness/{day}",
            None,
            ["score", "level", "acuteLoad", "recoveryTime", "hrvWeeklyAverage", "feedbackShort"],
        ),
        (
            "HRV",
            "昨夜 HRV 均值、5 分钟峰值、周均值与个人基线区间",
            f"/hrv-service/hrv/{day}",
            None,
            ["lastNightAvg", "lastNight5MinHigh", "weeklyAvg", "status", "markerValue"],
        ),
        (
            "VO2max·体能年龄",
            "最大摄氧量与体能年龄",
            f"/metrics-service/metrics/maxmet/daily/{day}/{day}",
            None,
            ["vo2MaxValue", "fitnessAge", "maxMet"],
        ),
        (
            "耐力分",
            "Endurance Score 与分级阈值（精英/优秀/良好…）",
            "/metrics-service/metrics/endurancescore",
            {"calendarDate": day},
            ["overallScore", "classification", "vo2Max", "vo2MaxPreciseValue"],
        ),
        (
            "爬坡分",
            "Hill Score 及其耐力/力量子分",
            "/metrics-service/metrics/hillscore",
            {"calendarDate": day},
            ["overallScore", "enduranceScore", "strengthScore"],
        ),
        (
            "比赛成绩预测",
            "5K / 10K / 半马 / 全马预测完赛时间（2026-10 实测 404，端点可能已下线）",
            "/metrics-service/metrics/racepredictions",
            {"calendarDate": day},
            ["timePredictorType", "predictedTime", "predictedTimeInSeconds"],
        ),
        (
            "乳酸阈·跑步",
            "乳酸阈心率/配速/功率，用来校准训练区间",
            "/biometric-service/biometric/latestFunctionalThresholdPower/RUNNING",
            None,
            ["functionalThresholdPower", "speed", "heartRate", "power"],
        ),
        (
            "压力·身体电量",
            "全天逐点压力与身体电量曲线（数组较长，这里只看统计）",
            f"/wellness-service/wellness/dailyStress/{day}",
            None,
            ["maxStressLevel", "avgStressLevel", "stressValuesArray", "bodyBatteryValuesArray"],
        ),
        (
            "呼吸率",
            "全天呼吸频率统计",
            f"/wellness-service/wellness/daily/respiration/{day}",
            None,
            ["avgWakingRespirationValue", "highestRespirationValue", "lowestRespirationValue"],
        ),
        (
            "血氧",
            "全天血氧统计（高海拔训练后有用）",
            f"/wellness-service/wellness/daily/spo2/{day}",
            None,
            ["averageSpO2", "lowestSpO2", "averageSpO2AltitudeAdjusted"],
        ),
        (
            "体重体脂",
            "体重、BMI、体脂、体水分、肌肉量、内脏脂肪（需 Garmin 体脂秤）",
            f"/weight-service/weight/dayview/{day}",
            None,
            ["weight", "bmi", "bodyFat", "bodyWater", "muscleMass", "dateWeightList"],
        ),
        (
            "个人纪录",
            "5K / 10K / 半马 / 全马等历史最好成绩",
            "/personalrecord-service/personalrecord/prs",
            None,
            ["activityType", "value", "startTimeGmt"],
        ),
        (
            "设备列表",
            "已绑定设备、型号、固件版本、最后同步时间",
            "/device-service/deviceregistration/devices",
            None,
            ["deviceId", "productDisplayName", "deviceVersion", "lastUsedDeviceTime"],
        ),
    ]


def main():
    parser = argparse.ArgumentParser(description="探测 garth 可获取的 Garmin 指标")
    parser.add_argument("--token", help="garth token（base64），默认读 GARTH_TOKEN 环境变量")
    parser.add_argument("--domain", default="garmin.com", choices=["garmin.com", "garmin.cn"])
    parser.add_argument("--from-db", action="store_true", help="从数据库 t_base_connect 取 token")
    parser.add_argument("--connect-id", type=int, help="配合 --from-db 指定连接 id")
    parser.add_argument("--date", help="基准日 YYYY-MM-DD，默认昨天")
    parser.add_argument("--days", type=int, default=7, help="区间端点回看天数，默认 7")
    parser.add_argument("--only", help="只跑标签含这些关键字的项，逗号分隔")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--insecure", action="store_true", help="跳过证书校验（garmin.cn 默认已跳过）")
    parser.add_argument("--dump", action="store_true", help="保存每个端点的原始 JSON")
    args = parser.parse_args()

    token = args.token or os.environ.get("GARTH_TOKEN")
    domain = args.domain
    if args.from_db:
        token, db_domain = token_from_db(args.connect_id)
        if not args.domain or args.domain == "garmin.com":
            if db_domain == "garmin.cn" or "--domain" not in sys.argv:
                domain = db_domain

    if not token:
        print("拿不到 token：请设置 GARTH_TOKEN，或用 --token / --from-db。")
        sys.exit(2)

    base = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else date.today() - timedelta(days=1)
    )
    start = str(base - timedelta(days=max(args.days - 1, 0)))
    end = str(base)
    day = end

    client = build_client(token, domain, timeout=args.timeout, insecure=args.insecure)

    if args.dump:
        DUMP_DIR.mkdir(exist_ok=True)

    probes = build_probes(day, start, end)
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        probes = [p for p in probes if any(w in p[0] for w in wanted)]

    print(f"区域={domain}  基准日={day}  区间={start}~{end}  探测项={len(probes)}\n")
    print(f"{'状态':<4}{'标签':<20}结果")
    print("-" * 100)

    ok = 0
    for label, desc, path, params, keys in probes:
        try:
            data = client.connectapi(path, params=params) if params else client.connectapi(path)
            status = "OK"
            detail = summarize(data, keys)
            ok += 1
            if args.dump:
                safe = label.replace("/", "_")
                (DUMP_DIR / f"{safe}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
        except Exception as e:  # noqa: BLE001 - 探测脚本就是要看每一个的真实报错
            status = "FAIL"
            detail = f"{type(e).__name__}: {str(e)[:90]}"
        print(f"{status:<6}{label:<20}{detail}")
        print(f"      └ {desc}")

    print("-" * 100)
    print(f"成功 {ok}/{len(probes)}")
    if args.dump:
        print(f"原始响应已存到 {DUMP_DIR}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
