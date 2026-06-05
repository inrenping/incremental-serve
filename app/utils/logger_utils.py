from contextlib import contextmanager
import threading
import time
from datetime import datetime, timezone
from datetime import datetime
from typing import Optional
from fastapi import Request
from app.db.session import SessionLocal
from app.models.log_operation import OperationLog
from app.logger import logger


@contextmanager
def log_request(
    current_user=None,
    request: Optional[Request] = None,
    req_url: Optional[str] = None,
    req_method: Optional[str] = None,
    req_params: Optional[dict] = None,
    log_type: str = "THIRD_PARTY_CALL",
    module_name: str = "service",
    op_desc: str = "",
):
    """
    上下文管理器记录日志：
    - current_user: User 对象
    - request: FastAPI Request 对象
    - req_url / req_method: 第三方接口可手动传
    - req_params: 请求参数
    - ctx['response']: 上下文内赋值 response 对象，用于记录响应内容
    """
    start_time = time.time()
    context = {"req_params": req_params}
    try:
        yield context
    finally:
        duration_ms = int((time.time() - start_time) * 1000)

        # 用户信息
        user_id = getattr(current_user, "id", None)
        user_name = getattr(current_user, "user_name", None) or getattr(
            current_user, "username", None
        )

        # 请求信息
        url = None
        method = None
        ip_address = None
        user_agent = None

        if request:
            url = str(request.url)
            method = request.method
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")
            if req_params is None:
                try:
                    req_params = request.json()
                except Exception:
                    req_params = None
        else:
            url = req_url
            method = req_method
            user_agent = None
            ip_address = None

        # 响应
        response = context.get("response")
        resp_data = None
        if response is not None:
            try:
                resp_data = response.text[:1000] if response else None
            except Exception:
                resp_data = str(response)

        # 调用日志写入
        logger.log(
            user_id=user_id,
            user_name=user_name,
            log_type=log_type,
            module_name=module_name,
            op_desc=op_desc,
            req_url=url,
            req_method=method,
            req_params=req_params,
            ip_address=ip_address,
            user_agent=user_agent,
            duration_ms=duration_ms,
            resp_data=resp_data,
        )


def log_operation_async(
    user_id: str, log_type: str, module_name: str = "", op_desc: str = ""
):
    """
    异步记录用户操作日志，独立线程执行，不阻塞主线程
    """

    def _write_log():
        db = SessionLocal()
        try:
            log = OperationLog(
                user_id=user_id,
                log_type=log_type,
                module_name=module_name,
                op_desc=op_desc,
                created_at=datetime.now(timezone.utc),
            )
            db.add(log)
            db.commit()
        except Exception:
            pass
        finally:
            db.close()

    threading.Thread(target=_write_log, daemon=True).start()
