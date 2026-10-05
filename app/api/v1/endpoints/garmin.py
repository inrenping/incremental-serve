import os
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from app.db.session import get_db
from app.models.heart_rate_daily import HeartRateDaily
from app.models.heart_rate_detail import HeartRateDetail
from app.models.sleep_daily import SleepDaily
from app.models.sleep_detail import SleepDetail
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.core.security import get_current_user, get_current_user_optional
from app.core.cron_auth import CRON_SYNC_TOKEN_HEADER, _require_cron_token
from app.services import garmin_service, garmin_metrics_service
from app.services.user_service import get_user_by_username
from app.models.garmin_fitness_age import GarminFitnessAge
from app.models.garmin_personal_record import GarminPersonalRecord
from app.models.garmin_race_prediction import GarminRacePrediction
from app.models.garmin_training_status import GarminTrainingStatus

router = APIRouter()


def get_sync_principal(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> User:
    """同步接口的身份解析：登录用户优先，其次定时任务共享密钥。

    注意：写接口绝不回退到默认账号，匿名请求一律 401。
    """
    provided = request.headers.get(CRON_SYNC_TOKEN_HEADER)
    expected = os.getenv("CRON_SYNC_TOKEN")
    if provided and expected and secrets.compare_digest(provided, expected):
        target_email = os.getenv("CRON_SYNC_USER_EMAIL", "inrenping")
        cron_user = get_user_by_username(db, target_email)
        if cron_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务目标用户 {target_email} 不存在",
            )
        return cron_user

    if current_user is not None:
        return current_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _today_in_user_tz(current_user: User) -> str:
    """按用户时区取「今天」，避免 UTC 取日期在凌晨错一天。"""
    tz_name = getattr(current_user, "timezone", None) or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y-%m-%d")


def _find_garmin_cn_connect(db: Session, user_id: int) -> BaseConnect:
    connect = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == user_id,
            BaseConnect.source_type == "garmin",
            func.lower(BaseConnect.region) == "cn",
            BaseConnect.is_active == True,
        )
        .first()
    )
    if connect is None:
        raise HTTPException(
            status_code=404,
            detail="未找到已激活的 Garmin CN 连接",
        )
    return connect

# --- 定义前端请求的数据结构 ---


class OAuth1Data(BaseModel):
    """Garmin OAuth 1.0 凭证数据模型"""

    oauth_token: str
    oauth_token_secret: str


class OAuth2Data(BaseModel):
    """Garmin OAuth 2.0 令牌数据模型"""

    access_token: str
    refresh_token: str
    expires_at: float
    refresh_token_expires_at: float


class TokenData(BaseModel):
    """完整的 Garmin Token 数据包，包含 OAuth1 和 OAuth2"""

    oauth1: OAuth1Data
    oauth2: OAuth2Data
    session: Optional[Any] = None


class GarminSaveRequest(BaseModel):
    """保存 Garmin 授权配置的请求体"""

    tokenData: TokenData
    username: Optional[str] = None
    password: Optional[str] = None


class GarminLoginRequest(BaseModel):
    """高驰登录请求模型"""

    email: str
    password: str


@router.post("/login")
def login_garmin(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    通过用户名密码模拟登录
    """
    configs = garmin_service.get_garmin_configs(db, current_user.id)
    if not configs:
        raise HTTPException(
            status_code=404, detail="No Garmin configuration found for the user."
        )
    return {
        "status": "success",
        "data": [
            {
                "username": config.garmin_account,
                "password": config.garmin_password,
                "platform": config.region,
            }
            for config in configs
        ],
    }


@router.post("/getGarminSecretString")
def get_garmin_secret_string(
    connect_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    模拟 Garmin 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 garmin_connect 表。
    """
    try:
        updated_auth = garmin_service.refresh_garmin_secret_string(
            connect_id, db, current_user
        )
        return {
            "status": "success",
            "data": {
                "garmin_user_id": updated_auth.user_id,
                "region_id": updated_auth.region,
            },
        }
    except HTTPException as e:
        raise e


@router.post("/getGarminAccessTokenBySecertString")
def get_garmin_access_token_by_secret_string(
    connectId: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_connect = garmin_service.refresh_garmin_access_token(
        connectId, db, current_user
    )
    if not base_connect:
        return {"status": "error", "message": "未找到高驰授权配置，请先登录获取授权。"}
    return {"status": "success", "access_token": base_connect.access_token}


@router.post("/saveConfig")
def save_garmin_config(
    payload: GarminSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从前端保存 Garmin 授权配置。
    解析 JWT 自动识别 region 和 garmin_guid，并绑定到当前用户。
    """
    garmin_auth = garmin_service.save_garmin_connection(
        db=db,
        user_id=current_user.id,
        token_data=payload.tokenData,
        username=payload.username,
        password=payload.password,
    )

    return {
        "status": "success",
        "data": {"region": garmin_auth.region, "garmin_guid": garmin_auth.guid},
    }


@router.get("/refreshGarminActivityCount")
def refresh_garmin_activity_count(db: Session = Depends(get_db)):
    """刷新数字"""
    garmin_service.refresh_garmin_activity_count(db)
    return {"status": "success"}


@router.get("/saveNewActivities")
def save_new_activities(
    region: str = "CN",
    new_count: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从佳明接口获取最新的运动数据并保存。
    默认获取最近的 10 条记录，用于快速增量同步。
    """
    return garmin_service.sync_new_garmin_activities(
        db=db, user_id=current_user.id, region=region, limit=new_count
    )


@router.get("/downloadActivity/{id}")
def download_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    下载佳明运动记录的原文件，支持流式传输。
    """
    # 1. 获取 Response 对象（此时连接仍处于 open 状态）
    file_response, filename = garmin_service.get_garmin_activity_download_info(
        db, current_user, id
    )

    # 2. 定义生成器，确保在传输完成后关闭连接
    def stream_contents():
        try:
            # 这里的 .iter_content 是 requests 对象的方法
            for chunk in file_response.iter_content(chunk_size=8192):
                yield chunk
        finally:
            # 无论传输成功还是客户端断开，都关闭与佳明的连接
            file_response.close()

    return StreamingResponse(
        stream_contents(),
        media_type="application/zip",  # 佳明原始文件通常是压缩包
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/uploadCorosActivity2Garmin/{id}")
def upload_coros_activity_to_garmin(
    id: int,
    region: str = Query("CN", description="上传目标佳明账号区域: CN 或 GLOBAL"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    按 /coros/downloadActivity 同源流程从高驰获取 FIT，再上传到当前用户绑定的佳明账号。
    """
    region = region.upper()
    return garmin_service.sync_coros_to_garmin(
        db, current_user, id, target_connect_id=region
    )


@router.get("/syncDailyHeartRate")
def sync_daily_heart_rate(
    date: str = Query(None, description="日期，格式 YYYY-MM-DD，默认为今天"),
    current_user: User = Depends(get_sync_principal),
    db: Session = Depends(get_db),
):
    """
    获取并保存指定日期的 Garmin 心率数据到数据库。

    从 Garmin 获取心率汇总和明细数据，存入 t_heart_rate_daily 和 t_heart_rate_detail 表。
    数据归属当前登录用户；定时任务可改用 X-Sync-Token 头鉴权。
    如果数据库中已有当前用户同一天的数据，则更新；否则插入新记录。
    """
    connect = _find_garmin_cn_connect(db, current_user.id)

    if date is None:
        date = _today_in_user_tz(current_user)

    garmin_service.save_garmin_daily_heart_rate(
        connect_id=connect.id,
        date=date,
        db=db,
        current_user=current_user,
    )
    return {"status": "success"}


@router.get("/getDailyHeartRate")
def get_daily_heart_rate(
    date_str: str = Query(None, description="日期，格式 YYYY-MM-DD，默认为今天"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    获取指定日期的当天心率数据，包含每日汇总（HeartRateDaily）和心率明细（HeartRateDetail）。

    优先使用当前登录用户（依据请求中的鉴权令牌）；
    未登录或凭据无效时，回退到默认账号 inrenping（兼容无 token 的调用方）。
    心率明细通过当前用户当日汇总记录（daily_id）关联查询，天然按用户隔离。
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        query_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="日期格式错误，请使用 YYYY-MM-DD 格式",
        )

    # 未登录时回退到默认账号 inrenping
    if current_user is None:
        current_user = get_user_by_username(db, "inrenping")
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="默认用户 inrenping 不存在",
        )

    # 查询每日心率汇总（calendar_date 是 Date 类型，无时区问题；按当前用户过滤）
    daily_record = (
        db.query(HeartRateDaily)
        .filter(
            HeartRateDaily.user_id == current_user.id,
            HeartRateDaily.calendar_date == query_date,
        )
        .first()
    )

    # 仅返回当前用户当日汇总下的采样明细（通过 daily_id 关联，天然按用户隔离）
    detail_records = (
        db.query(HeartRateDetail)
        .filter(HeartRateDetail.daily_id == daily_record.id)
        .order_by(HeartRateDetail.sample_time)
        .all()
        if daily_record is not None
        else []
    )

    return {
        "status": "success",
        "data": {
            "daily": (
                {
                    "id": daily_record.id,
                    "user_id": daily_record.user_id,
                    "calendar_date": daily_record.calendar_date.isoformat(),
                    "max_heart_rate": daily_record.max_heart_rate,
                    "min_heart_rate": daily_record.min_heart_rate,
                    "resting_heart_rate": daily_record.resting_heart_rate,
                    "last_seven_days_avg_resting_heart_rate": daily_record.last_seven_days_avg_resting_heart_rate,
                    "created_at": (
                        daily_record.created_at.isoformat()
                        if daily_record.created_at
                        else None
                    ),
                    "updated_at": (
                        daily_record.updated_at.isoformat()
                        if daily_record.updated_at
                        else None
                    ),
                }
                if daily_record
                else None
            ),
            "details": [
                {
                    "sample_time": detail.sample_time.isoformat(),
                    "heart_rate": detail.heart_rate,
                }
                for detail in detail_records
            ],
        },
    }


# ==================== 睡眠 ====================


@router.get("/syncDailySleep")
def sync_daily_sleep(
    date: str = Query(None, description="日期，格式 YYYY-MM-DD，默认为今天"),
    current_user: User = Depends(get_sync_principal),
    db: Session = Depends(get_db),
):
    """
    获取并保存指定日期的佳明睡眠数据。

    口径：date 传**起床那天**（佳明 calendarDate）。
    例如 10/2 晚上睡到 10/3 早上，应传 2026-10-03。
    当天没有任何睡眠记录时返回 has_data=false，不报错。
    """
    connect = _find_garmin_cn_connect(db, current_user.id)

    if date is None:
        date = _today_in_user_tz(current_user)

    result = garmin_service.save_garmin_daily_sleep(
        connect_id=connect.id,
        date=date,
        db=db,
        current_user=current_user,
    )
    return {"status": "success", "data": result}


@router.get("/syncMonthlySleep")
def sync_monthly_sleep(
    month: str = Query(None, description="月份，格式 YYYY-MM，默认为当月"),
    current_user: User = Depends(get_sync_principal),
    db: Session = Depends(get_db),
):
    """获取并保存指定月份的每一天睡眠数据（逐日同步，复用 save_garmin_daily_sleep）。

    口径：month 是佳明口径的归属月（起床那天所在月）。
    当前月只同步到今天（含），避免无谓请求未来日期。
    """
    connect = _find_garmin_cn_connect(db, current_user.id)
    if month is None:
        month = _today_in_user_tz(current_user)[:7]
    try:
        result = garmin_service.sync_monthly_sleep(
            connect_id=connect.id,
            month=month,
            db=db,
            current_user=current_user,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return {"status": "success", "data": result}


@router.get("/getDailySleep")
def get_daily_sleep(
    date_str: str = Query(None, description="日期，格式 YYYY-MM-DD，默认为今天"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    获取指定日期的睡眠汇总 + 阶段片段。

    与心率读接口一致：未登录时回退到默认账号 inrenping（兼容分享图等无 token 调用方）。
    阶段片段通过 daily_id 关联查询，天然按用户隔离。
    """
    if current_user is None:
        current_user = get_user_by_username(db, "inrenping")
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="默认用户 inrenping 不存在",
        )
    if date_str is None:
        date_str = _today_in_user_tz(current_user)

    try:
        query_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="日期格式错误，请使用 YYYY-MM-DD 格式",
        )

    daily_record = (
        db.query(SleepDaily)
        .filter(
            SleepDaily.user_id == current_user.id,
            SleepDaily.calendar_date == query_date,
        )
        .first()
    )

    detail_records = (
        db.query(SleepDetail)
        .filter(SleepDetail.daily_id == daily_record.id)
        .order_by(SleepDetail.start_at)
        .all()
        if daily_record is not None
        else []
    )

    def _iso(value):
        return value.isoformat() if value else None

    return {
        "status": "success",
        "data": {
            "daily": (
                {
                    "calendar_date": daily_record.calendar_date.isoformat(),
                    "sleep_start_at": _iso(daily_record.sleep_start_at),
                    "sleep_end_at": _iso(daily_record.sleep_end_at),
                    "local_offset_minutes": daily_record.local_offset_minutes,
                    "sleep_time_seconds": daily_record.sleep_time_seconds,
                    "nap_time_seconds": daily_record.nap_time_seconds,
                    "deep_sleep_seconds": daily_record.deep_sleep_seconds,
                    "light_sleep_seconds": daily_record.light_sleep_seconds,
                    "rem_sleep_seconds": daily_record.rem_sleep_seconds,
                    "awake_sleep_seconds": daily_record.awake_sleep_seconds,
                    "unmeasurable_sleep_seconds": daily_record.unmeasurable_sleep_seconds,
                    "awake_count": daily_record.awake_count,
                    "sleep_score": daily_record.sleep_score,
                    "average_sp_o2_value": daily_record.average_sp_o2_value,
                    "average_respiration_value": daily_record.average_respiration_value,
                    "avg_sleep_stress": daily_record.avg_sleep_stress,
                    "updated_at": _iso(daily_record.updated_at),
                }
                if daily_record
                else None
            ),
            "levels": [
                {
                    "start_at": level.start_at.isoformat(),
                    "end_at": level.end_at.isoformat(),
                    "duration_seconds": level.duration_seconds,
                    "activity_level": level.activity_level,
                }
                for level in detail_records
            ],
        },
    }


@router.get("/getMonthlySleep")
def get_monthly_sleep(
    month: str = Query(None, description="月份，格式 YYYY-MM，默认为当月"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    获取指定月份每天的睡眠汇总（不含阶段片段，避免 payload 过大）。

    只返回库里已有的日期；缺失的日期前端保持空白，不自动补数据。
    """
    if current_user is None:
        current_user = get_user_by_username(db, "inrenping")
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="默认用户 inrenping 不存在",
        )

    if month is None:
        month = _today_in_user_tz(current_user)[:7]

    try:
        year, mon = (int(part) for part in month.split("-"))
        month_start = date(year, mon, 1)
        next_year, next_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
        month_end = date(next_year, next_mon, 1)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="月份格式错误，请使用 YYYY-MM 格式",
        )

    records = (
        db.query(SleepDaily)
        .filter(
            SleepDaily.user_id == current_user.id,
            SleepDaily.calendar_date >= month_start,
            SleepDaily.calendar_date < month_end,
        )
        .order_by(SleepDaily.calendar_date)
        .all()
    )

    def _iso(value):
        return value.isoformat() if value else None

    # 阶段片段用于月报环形图按真实时间定位，一次查完再分组
    daily_ids = [record.id for record in records]
    levels_by_daily: dict = {}
    if daily_ids:
        for level in (
            db.query(SleepDetail)
            .filter(SleepDetail.daily_id.in_(daily_ids))
            .order_by(SleepDetail.daily_id, SleepDetail.start_at)
            .all()
        ):
            levels_by_daily.setdefault(level.daily_id, []).append(
                {
                    "start_at": level.start_at.isoformat(),
                    "end_at": level.end_at.isoformat(),
                    "duration_seconds": level.duration_seconds,
                    "activity_level": level.activity_level,
                }
            )

    return {
        "status": "success",
        "data": {
            "month": month,
            "days": [
                {
                    "calendar_date": record.calendar_date.isoformat(),
                    "sleep_start_at": _iso(record.sleep_start_at),
                    "sleep_end_at": _iso(record.sleep_end_at),
                    "sleep_time_seconds": record.sleep_time_seconds,
                    "deep_sleep_seconds": record.deep_sleep_seconds,
                    "light_sleep_seconds": record.light_sleep_seconds,
                    "rem_sleep_seconds": record.rem_sleep_seconds,
                    "awake_sleep_seconds": record.awake_sleep_seconds,
                    "awake_count": record.awake_count,
                    "sleep_score": record.sleep_score,
                    "levels": levels_by_daily.get(record.id, []),
                }
                for record in records
            ],
        },
    }


# ==================== 体能指标 ====================
# 训练状态·负荷 / 体能年龄 / 个人纪录 / 比赛成绩预测
# 四张表都是「一人一份最新快照」，重复同步即覆盖，不记历史。


def _find_master_garmin_connect(db: Session, user_id: int) -> BaseConnect:
    """取该用户主账号（t_base_connect.master=True）那条佳明连接。"""
    connect = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == user_id,
            BaseConnect.source_type == "garmin",
            BaseConnect.master == True,  # noqa: E712 - SQLAlchemy 需要 == True
            BaseConnect.is_active == True,  # noqa: E712
        )
        .first()
    )
    if connect is None:
        raise HTTPException(status_code=404, detail="未找到主账号为佳明的已激活连接")
    return connect


def _num(value):
    """Decimal 转 float，避免前端拿到字符串。"""
    return float(value) if value is not None else None


@router.get("/syncFitnessMetrics")
def sync_fitness_metrics(
    date: str = Query(None, description="日期，格式 YYYY-MM-DD，默认按用户时区取今天"),
    current_user: User = Depends(get_sync_principal),
    db: Session = Depends(get_db),
):
    """同步当前用户主账号（佳明）的四项体能指标。

    四项：训练状态·负荷、体能年龄、个人纪录、比赛成绩预测。
    单项失败不影响其他项，失败原因写在对应字段的 reason 里。
    比赛预测端点对无手表数据的账号会 404，属正常情况，表里保留上次的值。
    """
    connect = _find_master_garmin_connect(db, current_user.id)
    if date is None:
        date = _today_in_user_tz(current_user)

    result = garmin_metrics_service.sync_garmin_fitness_metrics(
        connect_id=connect.id, db=db, current_user=current_user, date=date
    )
    return {"status": "success", "data": result}


@router.get("/syncFitnessMetricsAll")
def sync_fitness_metrics_all(
    request: Request,
    date: str = Query(None, description="日期，格式 YYYY-MM-DD，默认按各用户时区取今天"),
    db: Session = Depends(get_db),
):
    """批量同步：所有「主账号是佳明」的用户各跑一遍。

    定时任务专用，需要 X-Sync-Token 头，普通登录用户不允许触发全量同步。
    """
    _require_cron_token(request)
    summary = garmin_metrics_service.sync_fitness_metrics_for_all_masters(
        db=db, date=date
    )
    return {"status": "success", "data": summary}


@router.get("/getFitnessMetrics")
def get_fitness_metrics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """读取当前用户已同步的体能指标快照，供前端页面展示。"""
    status = (
        db.query(GarminTrainingStatus)
        .filter(GarminTrainingStatus.user_id == current_user.id)
        .first()
    )
    fitness_age = (
        db.query(GarminFitnessAge)
        .filter(GarminFitnessAge.user_id == current_user.id)
        .first()
    )
    records = (
        db.query(GarminPersonalRecord)
        .filter(GarminPersonalRecord.user_id == current_user.id)
        .order_by(GarminPersonalRecord.type_id, GarminPersonalRecord.activity_type)
        .all()
    )
    predictions = (
        db.query(GarminRacePrediction)
        .filter(GarminRacePrediction.user_id == current_user.id)
        .order_by(GarminRacePrediction.race_type)
        .all()
    )

    return {
        "status": "success",
        "data": {
            "training_status": (
                {
                    "calendar_date": (
                        status.calendar_date.isoformat() if status.calendar_date else None
                    ),
                    "training_status": status.training_status,
                    "training_status_feedback_phrase": status.training_status_feedback_phrase,
                    "training_paused": status.training_paused,
                    "weekly_training_load": _num(status.weekly_training_load),
                    "daily_training_load_acute": _num(status.daily_training_load_acute),
                    "daily_training_load_chronic": _num(status.daily_training_load_chronic),
                    "acute_chronic_workload_ratio": _num(status.acute_chronic_workload_ratio),
                    "acwr_status": status.acwr_status,
                    "load_tunnel_min": _num(status.load_tunnel_min),
                    "load_tunnel_max": _num(status.load_tunnel_max),
                    "vo2_max_value": _num(status.vo2_max_value),
                    "vo2_max_running": _num(status.vo2_max_running),
                    "vo2_max_cycling": _num(status.vo2_max_cycling),
                    "synced_at": status.synced_at.isoformat() if status.synced_at else None,
                }
                if status
                else None
            ),
            "fitness_age": (
                {
                    "calendar_date": (
                        fitness_age.calendar_date.isoformat()
                        if fitness_age.calendar_date
                        else None
                    ),
                    "fitness_age": _num(fitness_age.fitness_age),
                    "vo2_max_value": _num(fitness_age.vo2_max_value),
                    "max_met": _num(fitness_age.max_met),
                    "synced_at": (
                        fitness_age.synced_at.isoformat() if fitness_age.synced_at else None
                    ),
                }
                if fitness_age
                else None
            ),
            "personal_records": [
                {
                    "type_id": record.type_id,
                    "type_key": record.type_key,
                    "activity_type": record.activity_type,
                    "unit": record.unit,
                    "value": _num(record.value),
                    "value_seconds": _num(record.value_seconds),
                    "value_meters": _num(record.value_meters),
                    "activity_name": record.activity_name,
                    "achieved_at": record.achieved_at.isoformat() if record.achieved_at else None,
                    "activity_id": record.activity_id,
                }
                for record in records
            ],
            "race_predictions": [
                {
                    "race_type": prediction.race_type,
                    "distance_meters": _num(prediction.distance_meters),
                    "predicted_seconds": _num(prediction.predicted_seconds),
                    "predicted_time_text": prediction.predicted_time_text,
                    "synced_at": (
                        prediction.synced_at.isoformat() if prediction.synced_at else None
                    ),
                }
                for prediction in predictions
            ],
        },
    }
