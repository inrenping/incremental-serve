import os
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

@router.post("/relogin")
def login_garmin(
    configId: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    模拟 garmin 登录并将认证信息存入数据库。
    成功后将保存 accessToken 到 garmin_connect 表。
    """

    config = garmin_service.get_garmin_config(db, current_user, configId)
    if not config:
        raise HTTPException(status_code=404, detail="No Garmin configuration found for the user.")
        
    print(f"查询到的 Garmin 配置: {config.region}")    
    
    # 3. 解密密码
    raw_password = CryptoUtils.decrypt(config.garmin_password, SECRET_KEY)
    print(f"正在使用佳明 {config.region} 站点进行登录...{config.garmin_account},{raw_password}")
    
    # 1. 重置客户端
    garth.client = Client()
    
    # 2. 配置域名
    is_cn = config.region.upper() == "CN"
    if is_cn:
        garth.configure(domain="garmin.cn")
    else:
        garth.configure(domain="garmin.com")

    # ==================== 【降维打击：全底层网络请求流净化补丁】 ====================
    # 备份全局 client 最底层的普通 request 方法（无论是 GET/POST 都会走这里）
    _original_request = garth.client.request

    def patched_request(method, url, **kwargs):
        """
        不论上层怎么重构，在流量真正流出服务器的最后一米，对中国区的请求进行脱敏和净化
        """
        if is_cn:
            # 1. 精准清洗 Headers，干掉所有残留的国际区 SSO 跨域源
            if "headers" in kwargs and isinstance(kwargs["headers"], dict):
                headers = kwargs["headers"]
                # 如果残留了国际区的 Origin 或 Referer，强行纠正或删除
                if "Origin" in headers and "garmin.com" in headers["Origin"]:
                    headers["Origin"] = "https://sso.garmin.cn"
                if "Referer" in headers and "garmin.com" in headers["Referer"]:
                    headers["Referer"] = "https://sso.garmin.cn"
            else:
                # 如果压根没传 headers，初始化一个，确保带上正确的国区环境
                kwargs["headers"] = {
                    "Origin": "https://sso.garmin.cn",
                    "Referer": "https://sso.garmin.cn"
                }

            # 2. 拦截并清洗请求体（Payload）中的 scope=all 隐患
            if "data" in kwargs and isinstance(kwargs["data"], dict):
                if kwargs["data"].get("scope") == "all":
                    kwargs["data"].pop("scope", None)
                    print("🚀 [Ultimate-Request-Patch] 成功剔除 data 中的 scope=all")
            elif "json" in kwargs and isinstance(kwargs["json"], dict):
                if kwargs["json"].get("scope") == "all":
                    kwargs["json"].pop("scope", None)
                    print("🚀 [Ultimate-Request-Patch] 成功剔除 json 中的 scope=all")
            elif "params" in kwargs and isinstance(kwargs["params"], dict):
                if kwargs["params"].get("scope") == "all":
                    kwargs["params"].pop("scope", None)
                    print("🚀 [Ultimate-Request-Patch] 成功剔除 params 中的 scope=all")

        return _original_request(method, url, **kwargs)

    # 替换全局最底层的请求收口
    garth.client.request = patched_request
    # ==============================================================================

    try:
        # 执行登录
        garth.login(config.garmin_account, raw_password)
        
        # 强制安全性二次校验
        if not garth.client.oauth2_token:
            raise ValueError("Garth 未能成功装载 OAuth2Token 凭证。")
        
        # 4. 确保拿到了 Token 再进行 dumps 和解析
        secret_string = garth.client.dumps()
        print("_" * 50)
        print(secret_string)
        print("_" * 50)
        print(f"当前登录的用户: {garth.client.username}")
        
        # 保留你原有的 Base64 解码与 JSON 解析逻辑
        decoded_bytes = base64.b64decode(secret_string)
        decoded_str = decoded_bytes.decode('utf-8')
        token_data_list = json.loads(decoded_str)
        token_data = token_data_list[0]

        print("=" * 40)
        print(token_data)
        print("=" * 40)
        
    except Exception as e:
        # 捕获异常，方便调试
        print(f"❌ Garth login 实际捕获到的错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Garmin 认证失败: {str(e)}")

    # 保持你原有的数据库存储和响应返回逻辑完全不动
    garmin_auth = garmin_service.perform_garmin_login(
        db=db,
        user=current_user,
        account=config.garmin_account,
        password_encrypted=config.garmin_password
    )
    
    return {
        "status": "success",
        "data": {
            "garmin_user_id": garmin_auth.user_id,
            "region_id": garmin_auth.region
        }
    }

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