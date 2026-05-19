import os
os.environ["GARTH_TELEMETRY_ENABLED"] = "false"
from typing import Any, Optional, Tuple
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, config

from app.db.session import get_db
from app.models.user import User
from app.core.security import get_current_user
from app.services import garmin_service
import garth
from garth.http import Client
import base64
import json
import argparse
from app.utils.crypto_utils import CryptoUtils

router = APIRouter()

SECRET_KEY = os.getenv("SECRET_KEY")

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
    username: Optional[str] = None # 对应 garmin_account
    password: Optional[str] = None # 对应 garmin_password

class GarminLoginRequest(BaseModel):
    """高驰登录请求模型"""
    email: str
    password: str

GARMIN_COM_URL_DICT = {
    "SSO_URL_ORIGIN": "https://sso.garmin.com",
    "SSO_URL": "https://sso.garmin.com/sso",
    "MODERN_URL": "https://connectapi.garmin.com",
    "SIGNIN_URL": "https://sso.garmin.com/sso/signin",
    "UPLOAD_URL": "https://connectapi.garmin.com/upload-service/upload/",
    "ACTIVITY_URL": "https://connectapi.garmin.com/activity-service/activity/{activity_id}",
}

GARMIN_CN_URL_DICT = {
    "SSO_URL_ORIGIN": "https://sso.garmin.com",
    "SSO_URL": "https://sso.garmin.cn/sso",
    "MODERN_URL": "https://connectapi.garmin.cn",
    "SIGNIN_URL": "https://sso.garmin.cn/sso/signin",
    "UPLOAD_URL": "https://connectapi.garmin.cn/upload-service/upload/",
    "ACTIVITY_URL": "https://connectapi.garmin.cn/activity-service/activity/{activity_id}",
}

@router.post("/saveGarminSecretString")
def save_garmin_secret_string(
    configId: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    模拟 Garmin 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 garmin_connect 表。
    """

    if not SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="SECRET_KEY not configured"
        )

    garmin_config = garmin_service.get_garmin_config(
        db,
        current_user,
        configId
    )

    if not garmin_config:
        raise HTTPException(
            status_code=404,
            detail="No Garmin configuration found for the user."
        )

    print(f"查询到的 Garmin 配置: {garmin_config.region}")

    # 解密密码
    try:
        raw_password = CryptoUtils.decrypt(
            garmin_config.garmin_password,
            SECRET_KEY
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"密码解密失败: {str(e)}"
        )

    print(f"正在使用佳明 {garmin_config.region} 站点进行登录...")
    try:
        if garmin_config.region and str(garmin_config.region).upper() == "CN":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")

        # 直接传入数据库里查到的账号和解密后的密码
        garth.login(garmin_config.garmin_account, raw_password)
        
        # 导出凭证字符串
        secret_string = garth.client.dumps()
        print(f"成功获取到 secret_string {secret_string}")
        
    except garth.exc.GarthException as e:
        raise HTTPException(
            status_code=500,
            detail=f"佳明登录认证失败: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"佳明连接异常: {str(e)}"
        )
    # =========================================================================

    updated_auth = garmin_service.save_garmin_secret(
        db=db,
        connect_id=garmin_config.id, 
        username=garmin_config.garmin_account,
        password=garmin_config.garmin_password,
        secret_string=secret_string
    )

    return {
        "status": "success",
        "data": {
            "garmin_user_id": updated_auth.user_id,
            "region_id": updated_auth.region
        }
    }

@router.post("/saveGarminAccessToken") 
def save_garmin_access_token(
    configId: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):    
    # 获取用户的佳明配置
    garmin_config = garmin_service.get_garmin_config(
        db,
        current_user,
        configId
    )
    
    if not garmin_config or not garmin_config.secret_string:
        raise HTTPException(
            status_code=404,
            detail="找不到有效的佳明配置或凭证字符串(secret_string)"
        )

    try:
        # 1. 【核心修复】：直接注入全新的干净实例，彻底避免多用户串号，且不会触发只读属性报错
        garth.client = Client()
        
        # 2. 动态配置对应的域名服务器（重新实例化后必须重新 configure）
        if garmin_config.region and str(garmin_config.region).upper() == "CN":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")
            
        # 3. 载入数据库中保存的凭证字符串
        garth.client.loads(garmin_config.secret_string)
        
        # 4. 检查是否过期，过期则自动刷新
        if garth.client.oauth2_token.expired:
            print("OAuth2 token 已过期，自动刷新...")
            garth.client.refresh_oauth2()
            
            # 将新刷新的凭证持久化回数据库
            new_secret_string = garth.client.dumps()
            # garmin_service.update_garmin_secret_string(
            #     db=db, 
            #     config_id=garmin_config.id, 
            #     secret_string=new_secret_string
            # )
            print(f"已刷新 OAuth2 token 并更新 secret_string: {new_secret_string}")

        # 5. 成功获取并返回最新的 access_token
        oauth2_token = garth.client.oauth2_token
        if not oauth2_token or not oauth2_token.access_token:
            raise Exception("佳明 OAuth2 Token 或 access_token 字段为空")
            
        return {
            "status": "success",
            "access_token": oauth2_token.access_token
        }

    except Exception as e:
        print(f"获取 access_token 失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"佳明 Token 处理失败: {str(e)}"
        )

@router.get("/getConfig")
def get_garmin_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的 Garmin 授权配置列表。
    由于支持不同区域（CN/GLOBAL），一个用户可能拥有多个配置。
    """
    configs = garmin_service.get_garmin_configs(db, current_user.user_id)

    return {
        "status": "success",
        "data": [
            {
                "id": c.id,
                "region": c.region,
                "garmin_guid": c.garmin_guid,
                "garmin_display_name": c.garmin_display_name,
                "is_active": c.is_active,
                "last_synced_at": c.last_synced_at,
                "updated_at": c.updated_at
            }
            for c in configs
        ]
    }

@router.post("/saveConfig")
def save_garmin_config(
    payload: GarminSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从前端保存 Garmin 授权配置。
    解析 JWT 自动识别 region 和 garmin_guid，并绑定到当前用户。
    """
    data = garmin_service.save_garmin_auth_config(
        db=db,
        user_id=current_user.user_id,
        token_data=payload.tokenData,
        username=payload.username,
        password=payload.password
    )
    
    return {
        "status": "success",
        "data": data
    }

@router.get("/refreshGarminActivityCount")
def refresh_garmin_activity_count(
    db: Session = Depends(get_db)
):
    """刷新数字"""
    garmin_service.refresh_garmin_activity_count(db)
    return {"status": "success"}

@router.post("/login")
def login_garmin(    
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    给前端返回用户名密码，让前端去刷新认证
    """
    configs = garmin_service.get_garmin_configs(db, current_user.user_id)
    if not configs:
        raise HTTPException(status_code=404, detail="No Garmin configuration found for the user.")
    return {
        "status": "success",
        "data": [
            {
                "username": config.garmin_account,
                "password": config.garmin_password,
                "platform": config.region
            }
            for config in configs
        ]
    }

@router.get("/pullFullActivities")
def pull_full_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    return garmin_service.pull_full_garmin_activities(db, current_user, region,True)

@router.get("/pullNewActivities")
def pull_new_activities(
    region: str = "CN",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取全部运动数据并保存到本地数据库。
    使用分页参数 (start, limit) 循环拉取，直到数据取完。
    """
    return garmin_service.pull_full_garmin_activities(db, current_user, region,False)

@router.get("/saveNewActivities")
def save_new_activities(
    region: str = "CN",
    new_count:int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    从佳明接口获取最新的运动数据并保存。
    默认获取最近的 10 条记录，用于快速增量同步。
    """
    return garmin_service.sync_new_garmin_activities(
        db=db,
        user_id=current_user.user_id,
        region=region,
        limit=new_count
    )

@router.get("/downloadActivity/{id}")
def download_garmin_activity(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    下载佳明运动记录的原文件，支持流式传输。
    """
    # 1. 获取 Response 对象（此时连接仍处于 open 状态）
    file_response, filename = garmin_service.get_garmin_activity_download_info(db, current_user, id)
    
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
        media_type="application/zip", # 佳明原始文件通常是压缩包
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/uploadGarminActivity2Garmin/{id}")
def upload_garmin_activity_to_garmin(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    从活动所属区域下载 FIT，上传到另一区域佳明账号（国际↔中国）。
    需在两个区域分别完成绑定并持有有效 token。
    """
    return garmin_service.sync_garmin_to_garmin(db, current_user, id)

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
    region = region.upper();
    return garmin_service.sync_coros_to_garmin(db, current_user, id, target_region=region)