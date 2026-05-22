import os
os.environ["GARTH_TELEMETRY_ENABLED"] = "false"
import garth
from garth.http import Client
import io
import zipfile
import json
import base64
import requests
from datetime import datetime, timezone
from typing import Optional, Tuple, Any, List
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.base_connect import BaseConnect
from app.models.base_activity import BaseActivity
from app.models.user import User
from app.services import coros_service
from app.utils.crypto_utils import CryptoUtils
from app.utils.logger_utils import log_request

GARMIN_UPLOAD_API_DOMAIN = {"CN": "connectapi.garmin.cn", "GLOBAL": "connectapi.garmin.com"}

def get_garmin_config(db: Session, current_user: User,connect_id: Optional[int] = None) -> List[BaseConnect]:
    """获取指定用户的指定的佳明授权配置。"""
    return db.query(BaseConnect).filter(BaseConnect.user_id == current_user.user_id , BaseConnect.id == connect_id).first()

def get_garmin_configs(db: Session, current_user: User) -> List[BaseConnect]:
    """获取指定用户的所有佳明授权配置。"""
    return db.query(BaseConnect).filter(BaseConnect.user_id == current_user.user_id).all()

def update_garmin_count(db: Session, garmin_connect_id: int, total_count: int) -> bool:
    """更新 BaseConnect 中对应的 total_count 的值。"""
    garmin_auth = db.query(BaseConnect).filter(BaseConnect.id == garmin_connect_id).first()
    if garmin_auth:
        garmin_auth.total_count = total_count
        db.commit()
        return True
    return False

def save_garmin_secret(
    db: Session, 
    connect_id: int, 
    username: str, 
    password: str,
    secret_string: str  
):
    garmin_auth = db.query(BaseConnect).filter(
        BaseConnect.id == connect_id
    ).first()

    if not garmin_auth:
      raise HTTPException(status_code=404, detail="未找到对应的 Garmin 授权配置")
    garmin_auth.is_active = True
    garmin_auth.garmin_account = username
    garmin_auth.garmin_password = password     
    garmin_auth.updated_at = datetime.now(timezone.utc)
    garmin_auth.secret_string = secret_string

    db.commit()
    return garmin_auth


def save_garmin_auth_config(
    db: Session, 
    user_id: int, 
    connect_id: int,
    token_data: Any, 
    username: Optional[str] = None, 
    password: Optional[str] = None
) -> dict:
    """解析并保存 Garmin 授权配置。"""
    oauth2 = token_data.oauth2
    oauth1 = token_data.oauth1

    try:
        _, payload_b64, _ = oauth2.access_token.split('.')
        missing_padding = len(payload_b64) % 4
        if missing_padding:
            payload_b64 += '=' * (4 - missing_padding)
        
        decoded_payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        garmin_guid = decoded_payload.get("garmin_guid")
        iss = decoded_payload.get("iss", "")

        region = "GLOBAL" if "garmin.com" in iss else "CN"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析 Garmin Token 失败: {str(e)}")

    garmin_auth = db.query(BaseConnect).filter(
        BaseConnect.user_id == user_id,
        BaseConnect.region == region,
        BaseConnect.source_connect_id == connect_id
    ).first()

    if not garmin_auth:
        garmin_auth = BaseConnect(user_id=user_id, region=region, source_connect_id=connect_id)
        db.add(garmin_auth)

    garmin_auth.garmin_guid = garmin_guid
    garmin_auth.oauth_token = oauth1.oauth_token
    garmin_auth.oauth_token_secret = oauth1.oauth_token_secret
    garmin_auth.access_token = oauth2.access_token
    garmin_auth.refresh_token = oauth2.refresh_token
    garmin_auth.access_token_expires_at = datetime.fromtimestamp(oauth2.expires_at)
    garmin_auth.refresh_token_expires_at = datetime.fromtimestamp(oauth2.refresh_token_expires_at)
    garmin_auth.is_active = True
    garmin_auth.garmin_account = username
    garmin_auth.garmin_password = password     
    garmin_auth.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"region": region, "garmin_guid": garmin_guid}

def refresh_garmin_secret_string(db: Session, current_user: User, config_id: int):
    """使用保存的凭据模拟登录并刷新 secret_string。"""
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise HTTPException(status_code=500, detail="SECRET_KEY not configured")

    garmin_config = get_garmin_config(db, current_user, config_id)
    if not garmin_config:
        raise HTTPException(status_code=404, detail="No Garmin configuration found for the user.")

    # 解密密码
    try:
        raw_password = CryptoUtils.decrypt(garmin_config.garmin_password, secret_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密码解密失败: {str(e)}")

    try:
        if garmin_config.region and str(garmin_config.region).upper() == "CN":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")

        garth.login(garmin_config.garmin_account, raw_password)
        secret_string = garth.client.dumps()
        
    except garth.exc.GarthException as e:
        raise HTTPException(status_code=500, detail=f"佳明登录认证失败: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"佳明连接异常: {str(e)}")

    return save_garmin_secret(
        db=db,
        connect_id=garmin_config.id, 
        username=garmin_config.garmin_account,
        password=garmin_config.garmin_password,
        secret_string=secret_string
    )

def get_garmin_access_token(db: Session, current_user: User, connect_id: int) -> str:
    """通过 secret_string 获取有效的 Access Token，必要时自动刷新。"""
    garmin_config = get_garmin_config(db, current_user, connect_id)
    
    if not garmin_config or not garmin_config.secret_string:
        raise HTTPException(status_code=404, detail="找不到有效的佳明配置或凭证字符串")

    try:
        garth.client = Client()        
        if garmin_config.region and str(garmin_config.region).upper() == "CN":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")
            
        garth.client.loads(garmin_config.secret_string)        
        if garth.client.oauth2_token.expired:
            garth.client.refresh_oauth2()            
            new_secret_string = garth.client.dumps()
            secret_data = json.loads(new_secret_string)
            
            # 持久化新刷新的凭证
            # 注意：此处假定 save_garmin_auth_config 内部已处理 token_data 的解构或根据需求调整
            save_garmin_auth_config(
                db=db, 
                user_id=current_user.user_id,
                connect_id=garmin_config.id, 
                token_data=TokenDataHelper(secret_data), # 包装一下以适配其属性访问
                username=garmin_config.garmin_account,
                password=garmin_config.garmin_password
            )
            # 更新 secret_string 字段
            garmin_config.secret_string = new_secret_string
            db.commit()

        oauth2_token = garth.client.oauth2_token
        if not oauth2_token or not oauth2_token.access_token:
            raise Exception("佳明 OAuth2 Token 或 access_token 字段为空")
            
        return oauth2_token.access_token

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"佳明 Token 处理失败: {str(e)}")

class TokenDataHelper:
    """临时辅助类，用于将 dict 转换为 save_garmin_auth_config 期望的对象格式。"""
    def __init__(self, data):
        self.oauth1 = type('obj', (object,), data['oauth1'])
        self.oauth2 = type('obj', (object,), data['oauth2'])

def _sync_garmin_activities_internal(
    db: Session,
    config: BaseConnect,
    user: User,
    start: int = 0,
    limit: int = 100,
    incremental: bool = True
) -> Tuple[int, int]:
    """辅助方法：抓取并保存活动。"""
    base_url = "connect.garmin.cn" if config.region == "CN" else "connect.garmin.com"
    api_url = f"https://{base_url}/activitylist-service/activities/search/activities"
    
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "di-backend": base_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    api_params = {"start": start, "limit": limit}
    try:
        with log_request(
          current_user=user,
          req_url=api_url,
          req_method="GET",
          req_params=api_params,
          log_type="fileUrl",
          module_name="garmin",
          op_desc="佳明获取运动记录"
        ) as ctx:
          response = requests.get(api_url, params=api_params, headers=headers, timeout=10)
          ctx["response"] = response          
        response.raise_for_status()
        activities_data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步佳明数据失败: {str(e)}")

    if not activities_data or not isinstance(activities_data, list):
        return 0, 0

    activity_ids = [item.get("activityId") for item in activities_data if item.get("activityId")]
    existing_ids = {aid for (aid,) in db.query(BaseActivity.activity_id).filter(BaseActivity.activity_id.in_(activity_ids)).all()} if activity_ids else set()

    saved_count = 0
    for item in activities_data:
        activity_id = str(item.get("activityId"))
        if activity_id in existing_ids:
            if incremental:
                # 增量同步模式下，遇到已存在记录即停止本批次后续处理
                break
            continue
        
        # 转换时间
        start_time_str = item.get("startTimeGMT")
        start_time = None
        if start_time_str:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))

        new_activity = BaseActivity(
            user_id=user.user_id,
            source_provider="garmin",
            activity_id=activity_id,
            activity_name=item.get("activityName"),
            sport_type_standard=item.get("activityType", {}).get("typeKey"),
            sport_type_raw=item.get("activityType", {}).get("typeKey"),
            start_time=start_time,
            distance_meters=item.get("distance"),
            duration_seconds=item.get("duration"),
            moving_duration_seconds=item.get("movingDuration"),
            calories=item.get("calories"),
            average_hr=item.get("averageHR"),
            max_hr=item.get("maxHR"),
            average_cadence=item.get("averageRunningCadenceInStepsPerMinute") or item.get("averageBikingCadenceInRevPerMinute"),
            elevation_gain=item.get("elevationGain"),
            elevation_loss=item.get("elevationLoss"),
            garmin_activity_id=activity_id if config.region != "CN" else None,
            garmin_cn_activity_id=activity_id if config.region == "CN" else None
        )
        db.add(new_activity)
        saved_count += 1

    return len(activities_data), saved_count

def pull_full_garmin_activities(db: Session, user: User, region: str, incremental: bool = True) -> dict:
    """全量或者增量同步佳明活动。"""
    config = db.query(BaseConnect).filter(
        BaseConnect.user_id == user.user_id, 
        BaseConnect.region == region.upper()
    ).first()
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    start, limit, total_saved, total_fetched = 0, 100, 0, 0
    while True:
        fetched, saved = _sync_garmin_activities_internal(db, config, user, start, limit,incremental)
        total_fetched += fetched
        total_saved += saved
        if fetched < limit: break
        
        # 如果是增量同步且本页保存数量小于获取数量，说明遇到了重复数据，停止分页获取
        if incremental and saved < fetched:
            break
        start += limit

    if total_fetched: update_garmin_count(db, config.id, total_fetched)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "fetched_count": total_fetched, "saved_count": total_saved}

def sync_new_garmin_activities(db: Session, user_id: int, region: str, limit: int = 10) -> dict:
    """增量同步最新佳明活动。"""
    user = db.query(User).filter(User.user_id == user_id).first()
    config = db.query(BaseConnect).filter(
        BaseConnect.user_id == user_id, 
        BaseConnect.region == region.upper()
    ).first()
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    fetched, saved = _sync_garmin_activities_internal(db, config, user, 0, limit)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "fetched_count": fetched, "saved_count": saved}

def get_garmin_activity_download_info(db: Session, user: User, activity_id: int) -> Tuple[requests.Response, str]:
    """获取佳明文件下载响应对象（不直接读取内容）。"""
    ga = db.query(BaseActivity).filter(
        BaseActivity.user_id == user.user_id,
        BaseActivity.id == activity_id
    ).first()

    if not ga:
        raise HTTPException(status_code=404, detail="未找到活动记录")

    # 根据活动记录寻找对应的 Garmin 配置
    region = "CN" if ga.garmin_cn_activity_id else "GLOBAL"
    config = db.query(BaseConnect).filter(
        BaseConnect.user_id == user.user_id,
        BaseConnect.region == region
    ).first()
    
    if not config:
        raise HTTPException(status_code=404, detail="未找到有效的佳明授权或活动记录")
    
    base = "connect.garmin.cn" if region == "CN" else "connect.garmin.com"
    down_url = f"https://{base}/download-service/files/activity/{ga.activity_id}"
    headers = {
        "di-backend": base, 
        "Authorization": f"Bearer {config.access_token}",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        # 注意：这里不使用 with 语句，也不手动读取 .content
        # stream=True 允许我们后续分块读取
        resp = requests.get(down_url, headers=headers, timeout=30, stream=True)
        
        if resp.status_code != 200:
            resp.close() # 只有在失败时立即关闭
            raise HTTPException(status_code=resp.status_code, detail="佳明文件下载失败")
            
        return resp, f"{ga.activity_id}.zip"

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"网络请求错误: {str(e)}")

def parse_garmin_upload_response(response: requests.Response) -> Tuple[str, Optional[dict]]:
    """解析佳明上传接口响应。"""
    try:
        result = response.json()
    except:
        result = None

    if response.status_code == 202 and result:
        if (result.get("detailedImportResult") or {}).get("uploadId"):
            return "SUCCESS", result
        return "UPLOAD_REJECTED", result

    if response.status_code == 409 and result:
        try:
            msg = result.get("detailedImportResult", {}).get("failures", [{}])[0].get("messages", [{}])[0].get("content")
            if msg == "Duplicate Activity.": return "DUPLICATE_ACTIVITY", result
        except: pass
        return "UPLOAD_CONFLICT", result

    return "UPLOAD_FAILED" if result else "UPLOAD_EXCEPTION", result

def _upload_file_to_garmin(
    user: User, 
    target_config: BaseConnect, 
    file_data: bytes, 
    filename: str, 
    op_desc: str
) -> dict:
    """内部辅助方法：执行将文件上传到佳明服务器的通用逻辑。支持自动解压 zip 中的 fit 文件。"""
    
    # --- 新增逻辑：处理 ZIP 文件 ---
    upload_data = file_data
    upload_filename = filename

    if filename.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(file_data)) as z:
                # 获取 zip 中所有后缀为 .fit 的文件列表
                fit_files = [f for f in z.namelist() if f.lower().endswith('.fit')]
                
                if not fit_files:
                    return {"status": "error", "message": "Zip 压缩包中未找到 .fit 文件"}
                
                # 提取第一个 fit 文件的内容
                upload_filename = fit_files[0]
                upload_data = z.read(upload_filename)
        except zipfile.BadZipFile:
            return {"status": "error", "message": "无效的 Zip 文件"}
    # ----------------------------

    target_region = target_config.region
    api_domain = GARMIN_UPLOAD_API_DOMAIN.get(target_region, "connectapi.garmin.com")
    headers = {
        "Authorization": f"Bearer {target_config.access_token}", 
        "di-backend": "connect.garmin.cn" if target_region == "CN" else "connect.garmin.com"
    }
    url = f"https://{api_domain}/upload-service/upload"
    
    with log_request(
        current_user=user,
        req_url=url,
        req_method="POST",
        req_params=None,
        log_type="upload",
        module_name="garmin",
        op_desc=op_desc
    ) as ctx:
        # 使用处理后的 upload_filename 和 upload_data
        resp = requests.post(
            url, 
            headers=headers, 
            files={"file": (upload_filename, upload_data, "application/octet-stream")}, 
            timeout=60
        )
        ctx["response"] = resp
        
    status, json_res = parse_garmin_upload_response(resp)
    return {
        "status": "success", 
        "upload_status": status, 
        "target_region": target_region, 
        "http_status": resp.status_code, 
        "garmin_response": json_res,
        "actual_filename": upload_filename  # 可选：记录实际上传的文件名
    }

def sync_garmin_to_garmin(db: Session, user: User, activity_id: int) -> dict:
    """佳明之间同步逻辑。"""
    ga = db.query(BaseActivity).filter(BaseActivity.user_id == user.user_id, BaseActivity.id == activity_id).first()
    if not ga: raise HTTPException(status_code=404, detail="记录不存在")

    source_region = "CN" if ga.garmin_cn_activity_id else "GLOBAL"
    target_region = "GLOBAL" if source_region == "CN" else "CN"

    target_config = db.query(BaseConnect).filter(BaseConnect.user_id == user.user_id, BaseConnect.region == target_region).first()
    if not target_config: raise HTTPException(status_code=404, detail="目标区域未授权")

    file_resp, filename = get_garmin_activity_download_info(db, user, activity_id)
    return _upload_file_to_garmin(user, target_config, file_resp.content, filename, "佳明上传运动")

def sync_coros_to_garmin(db: Session, user: User, base_activity_id: int, target_region: str = "CN") -> dict:
    """高驰同步到佳明逻辑。"""
    target_config = db.query(BaseConnect).filter(
        BaseConnect.user_id == user.user_id,
        BaseConnect.region == target_region
    ).first()
    if not target_config: raise HTTPException(status_code=404, detail=f"目标佳明区域 {target_region} 未授权")

    file_resp, filename = coros_service.get_coros_activity_download_info(db, user, base_activity_id)
    
    return _upload_file_to_garmin(user, target_config, file_resp.content, filename, "上传佳明活动")

def refresh_garmin_activity_count(db: Session) -> dict:
    """刷新所有用户的佳明活动总数统计。"""     
    users = db.query(BaseConnect.user_id).distinct().all()
    for (user_id,) in users:
      garmin_auths = db.query(BaseConnect).filter(BaseConnect.user_id == user_id).all()
      for garmin_auth in garmin_auths:
        # 统计属于该 provider 的活动
        query = db.query(BaseActivity).filter(
            BaseActivity.user_id == user_id,
            BaseActivity.source_provider == "garmin"
        )
        if garmin_auth.region == "CN":
            query = query.filter(BaseActivity.garmin_cn_activity_id.isnot(None))
        else:
            query = query.filter(BaseActivity.garmin_activity_id.isnot(None))
            
        activity_count = query.count()
        update_garmin_count(db, garmin_auth.id, activity_count)
     