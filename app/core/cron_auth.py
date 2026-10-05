import os
import secrets

from fastapi import HTTPException, Request, status

# 定时任务专用鉴权头。GitHub Actions 等外部调度器拿不到登录态，
# 用共享密钥（环境变量 CRON_SYNC_TOKEN）换取全量同步权限。
CRON_SYNC_TOKEN_HEADER = "X-Sync-Token"


def _require_cron_token(request: Request) -> None:
    """批量同步会遍历所有用户，只允许定时任务密钥调用。

    共享密钥配置在环境变量 CRON_SYNC_TOKEN，与 garmin 同步任务共用。
    """
    provided = request.headers.get(CRON_SYNC_TOKEN_HEADER)
    expected = os.getenv("CRON_SYNC_TOKEN")
    if not provided or not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="仅允许定时任务密钥调用")
