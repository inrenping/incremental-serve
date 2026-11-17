import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.cron_auth import _require_cron_token
from app.db.session import get_db
from app.services import google_calendar_sync_service
from app.services.user_service import get_user_by_email, get_user_by_username

router = APIRouter()


def _get_service_account_info() -> dict:
    """
    从配置中获取 Google 服务账号信息。
    通过 GOOGLE_SERVICE_ACCOUNT_B64 环境变量传入 Base64 编码的 JSON 字符串。
    """
    sa = settings.GOOGLE_ACCOUNT_SERVICE_JSON
    if not sa:
        raise HTTPException(
            status_code=500,
            detail="Google 服务账号未配置，请设置 GOOGLE_SERVICE_ACCOUNT_B64",
        )

    return json.loads(sa)


@router.get("/events")
def get_calendar_events(
    start_date: str = Query(
        default=None, description="开始日期，格式 YYYY-MM-DD，默认当月1号"
    ),
    end_date: str = Query(
        default=None, description="结束日期，格式 YYYY-MM-DD，默认6个月后"
    ),
    calendar_id: str = Query("primary", description="日历 ID，默认为 primary"),
):
    """
    获取 Google Calendar 指定日期范围内的事件。

    不传 start_date/end_date 时默认查询：当月1号 ~ 6个月后。
    使用服务账号访问日历，需要将目标日历共享给服务账号邮箱。
    """
    from datetime import datetime, timedelta

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    service_account_info = _get_service_account_info()

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
    credentials = Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES
    )

    service = build("calendar", "v3", credentials=credentials)

    now = datetime.utcnow()
    if start_date:
        time_min = f"{start_date}T00:00:00Z"
    else:
        time_min = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if end_date:
        time_max = f"{end_date}T23:59:59Z"
    else:
        six_months_later = now + timedelta(days=6 * 30)
        time_max = six_months_later.replace(
            hour=23, minute=59, second=59, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Google Calendar API 调用失败: {str(e)}",
        )

    events = events_result.get("items", [])

    return {
        "status": "success",
        "data": events,
    }


@router.post("/syncCalendar")
def sync_calendar_to_google(
    request: Request,
    days: int = Query(
        7, ge=1, le=3650, description="回看天数（含今天），显式日期优先"
    ),
    start_date: str = Query(None, description="回填起始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="回填结束日期 YYYY-MM-DD"),
    year: int = Query(None, description="指定同步日期：年"),
    month: int = Query(None, description="指定同步日期：月"),
    day: int = Query(None, description="指定同步日期：日"),
    dry_run: bool = Query(False, description="只计算差异，不写 Google 日历"),
    db: Session = Depends(get_db),
):
    """把定时任务账号的睡眠（含逐条小睡）与运动同步到专用 Google 日历。

    仅允许 X-Sync-Token 调用（GitHub Actions / 手动运维）。
    year/month/day 同时传时只同步这一天；不传则按 days 回看（含今天）。
    dry_run=true 可先看新建/更新/跳过/删除数量，确认后再正式写入。
    """
    _require_cron_token(request)
    email = os.getenv("CRON_SYNC_USER_EMAIL", "inrenping")
    user = get_user_by_username(db, email) or get_user_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=404, detail=f"定时任务目标用户 {email} 不存在")

    try:
        result = google_calendar_sync_service.sync_calendar(
            db=db,
            user=user,
            days=days,
            start_date=start_date,
            end_date=end_date,
            year=year,
            month=month,
            day=day,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "data": result}
