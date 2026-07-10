import json
import os

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings

router = APIRouter()


def _get_service_account_info() -> dict:
    """
    从配置中获取 Google 服务账号信息。

    支持两种方式：
    1. GOOGLE_SERVICE_ACCOUNT_FILE 指向本地 JSON 文件路径
    2. GOOGLE_SERVICE_ACCOUNT_B64 传入 Base64 编码的 JSON 字符串
    """
    sa = settings.GOOGLE_ACCOUNT_SERVICE_JSON
    if not sa:
        raise HTTPException(
            status_code=500,
            detail="Google 服务账号未配置，请设置 GOOGLE_SERVICE_ACCOUNT_FILE 或 GOOGLE_SERVICE_ACCOUNT_B64",
        )

    if os.path.isfile(sa):
        with open(sa) as f:
            return json.load(f)
    return json.loads(sa)


@router.get("/events")
def get_calendar_events(
    calendar_id: str = Query("primary", description="日历 ID，默认为 primary"),
):
    """
    获取 Google Calendar 的事件。

    查询范围：当月1号 ~ 6个月后。
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
    time_min = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
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
