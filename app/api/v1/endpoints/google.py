import json

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings

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
