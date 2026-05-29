from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
from app.core.security import get_current_user
from app.services import coros_service
from app.models.base_connect import BaseConnect

router = APIRouter()


class CorosLoginRequest(BaseModel):
    """高驰登录请求模型"""

    id: int
    email: str
    password: str


@router.post("/login")
def login_coros(
    payload: CorosLoginRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    模拟高驰 (Coros) 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 coros_connect 表。
    """
    coros_auth = coros_service.perform_coros_login(
        id=payload.id,
        account=payload.email,
        encrypted_password=payload.password,
        db=db,
        current_user=current_user,
    )
    return {
        "status": "success",
        "data": {
            "coros_user_id": coros_auth.coros_user_id,
            "region_id": coros_auth.region,
        },
    }


@router.post("/relogin")
def relogin_coros(
    connect_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    模拟高驰 (Coros) 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 coros_connect 表。
    """
    if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}
    coros_config = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id, BaseConnect.id == connect_id
        )
        .first()
    )
    if not coros_config:
        return {"status": "error", "message": "未找到高驰授权配置，请先登录获取授权。"}
    coros_auth = coros_service.perform_coros_login(
        db=db,
        current_user=current_user,
        id=connect_id,
        account=coros_config.coros_account,
        encrypted_password=coros_config.coros_password_encrypted,
    )
    return {
        "status": "success",
        "data": {
            "coros_user_id": coros_auth.coros_user_id,
            "region_id": coros_auth.region,
        },
    }


@router.get("/downloadActivity/{id}")
def download_coros_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    下载高驰运动记录的 FIT 文件。
    流程：1. 请求元数据获取下载 URL -> 2. 执行 StreamingResponse 流式下载文件。
    """
    file_response, filename = coros_service.get_coros_activity_download_info(
        db, current_user, id
    )
    return StreamingResponse(
        file_response.iter_content(chunk_size=8192),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/uploadGarminActivity2Coros/{id}")
def upload_garmin_activity_to_coros(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    跨平台同步：将佳明的活动记录同步至高驰。
    流程：从佳明下载 FIT 原文件 -> 调用高驰 import 接口上传。
    """
    return coros_service.sync_garmin_to_coros(db, current_user, id)
