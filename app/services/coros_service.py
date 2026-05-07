import os
import json
import requests
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.coros_connect import CorosConnect
from app.models.coros_activity import CorosActivity
from app.models.garmin_activity import GarminActivity
from app.services.oss.ali_oss_client import AliOssClient
from app.services.oss.aws_oss_client import AwsOssClient
from app.utils.coros_region_config import REGIONCONFIG
from app.utils.coros_sts_config import STS_CONFIG
from app.utils.md5_utils import calculate_md5_file
from app.utils.config import GARMIN_FIT_DIR

def get_team_api_base(region_id: str) -> str:
    """根据区域 ID 获取高驰 Team API 的基准 URL。"""
    try:
        rid = int(region_id)
        if rid in REGIONCONFIG:
            return REGIONCONFIG[rid]["teamapi"]
    except (ValueError, TypeError):
        pass
    return REGIONCONFIG.get(1, {}).get("teamapi", "https://teamapi.coros.com")

def update_coros_count(db: Session, coros_connect_id: int, total_count: int) -> bool:
    """更新 CorosConnect 中对应的 total_count。"""
    coros_auth = db.query(CorosConnect).filter(CorosConnect.id == coros_connect_id).first()
    if coros_auth:
        coros_auth.total_count = total_count
        db.commit()
        return True
    return False

def perform_coros_login(
    db: Session, 
    user_id: int, 
    account: str, 
    password_encrypted: str, 
    is_refresh: bool = False
) -> CorosConnect:
    """执行高驰登录逻辑并更新授权信息。"""
    coros_auth = db.query(CorosConnect).filter(CorosConnect.user_id == user_id).first()
    
    login_url = "https://teamcnapi.coros.com/account/login"
    login_data = {
        "account": account,
        "pwd": password_encrypted,
        "accountType": 2,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.39 Safari/537.36",
    }

    try:
        response = requests.post(login_url, json=login_data, headers=headers, timeout=10)
        response.raise_for_status()
        login_response = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{'刷新' if is_refresh else '登录'}失败: {str(e)}")

    if login_response.get("result") != "0000":
        if is_refresh and coros_auth:
            coros_auth.is_active = False
            db.commit()
        raise HTTPException(status_code=400, detail=f"高驰登录失败: {login_response.get('message')}")

    data = login_response.get("data", {})
    if not coros_auth:
        coros_auth = CorosConnect(user_id=user_id)
        db.add(coros_auth)

    coros_auth.coros_account = account
    coros_auth.coros_password_encrypted = password_encrypted 
    coros_auth.access_token = data.get("accessToken")
    coros_auth.coros_user_id = str(data.get("userId"))
    coros_auth.region = data.get("regionId")
    coros_auth.is_active = True
    coros_auth.updated_at = datetime.now(timezone.utc)
    db.commit()
    return coros_auth

def sync_coros_activities(db: Session, user_id: int, limit: Optional[int] = None) -> dict:
    """同步高驰运动记录。如果 limit 为 None 则全量同步，否则增量拉取。"""
    coros_auth = db.query(CorosConnect).filter(
        CorosConnect.user_id == user_id,
        CorosConnect.is_active == True
    ).first()

    if not coros_auth or not coros_auth.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权配置，请先绑定账号")

    base_url = get_team_api_base(str(coros_auth.region))
    headers = {"Accept": "application/json, text/plain, */*", "accesstoken": coros_auth.access_token}

    page_size = limit if limit else 100
    page_number = 1
    total_count = 0
    total_fetched = 0
    new_saved_count = 0

    while True:
        query_url = f"{base_url}/activity/query?size={page_size}&pageNumber={page_number}"
        print(f"请求高驰活动列表，URL: {query_url}, Headers: {headers}")
        try:
            response = requests.get(query_url, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"获取高驰数据失败: {str(e)}")

        if result.get("result") != "0000":
            raise HTTPException(status_code=400, detail=f"高驰 API 返回异常: {result.get('message')}")

        data = result.get("data", {})
        if page_number == 1:
            total_count = data.get("count", 0)
            update_coros_count(db, coros_auth.id, total_count)

        activities_list = data.get("dataList", [])
        if not activities_list:
            break

        # 批量查重
        label_ids = [str(item.get("labelId")) for item in activities_list if item.get("labelId")]
        existing_ids = set()
        if label_ids:
            existing_ids = {
                lid for (lid,) in db.query(CorosActivity.label_id)
                .filter(CorosActivity.label_id.in_(label_ids))
                .all()
            }

        for item in activities_list:
            label_id = str(item.get("labelId"))
            if label_id in existing_ids:
                continue

            start_dt = datetime.fromtimestamp(item.get("startTime", 0), tz=timezone.utc) if item.get("startTime") else None
            end_dt = datetime.fromtimestamp(item.get("endTime", 0), tz=timezone.utc) if item.get("endTime") else None

            new_activity = CorosActivity(
                user_id=user_id,
                coros_connect_id=coros_auth.id,
                label_id=label_id,
                name=item.get("name"),
                sport_type=item.get("sportType"),
                mode=item.get("mode"),
                distance=item.get("distance"),
                ascent=item.get("ascent"),
                descent=item.get("descent"),
                calories=item.get("calorie"),
                avg_hr=item.get("avgHr"),
                max_hr=item.get("maxHr"),
                workout_time=item.get("workoutTime"),
                total_time=item.get("totalTime"),
                start_time=start_dt,
                end_time=end_dt
            )
            db.add(new_activity)
            new_saved_count += 1

        total_fetched += len(activities_list)
        if limit or total_fetched >= total_count or len(activities_list) < page_size:
            break
        page_number += 1

    coros_auth.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success",
        "total_in_platform": total_count,
        "fetched_count": total_fetched,
        "new_saved_count": new_saved_count
    }

def get_coros_activity_download_info(db: Session, user_id: int, activity_id: int) -> Tuple[requests.Response, str]:
    """获取高驰运动记录的文件流及建议的文件名。"""
    coros_auth = db.query(CorosConnect).filter(user_id == user_id, CorosConnect.is_active == True).first()
    if not coros_auth:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权配置")

    activity = db.query(CorosActivity).filter(user_id == user_id, CorosActivity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="未找到运动记录")

    base_url = get_team_api_base(str(coros_auth.region))
    meta_url = f"{base_url}/activity/detail/download?labelId={activity.label_id}&sportType={activity.sport_type}&fileType=4"
    headers = {"accesstoken": coros_auth.access_token}

    meta_res = requests.post(meta_url, headers=headers, timeout=10).json()
    if meta_res.get("result") != "0000":
        raise HTTPException(status_code=400, detail=f"获取下载链接失败: {meta_res.get('message')}")

    download_url = meta_res.get("data", {}).get("fileUrl")
    file_response = requests.get(download_url, stream=True, timeout=30)
    file_response.raise_for_status()
    
    return file_response, f"coros_activity_{activity.label_id}.fit"

def _upload_fit_zip_to_coros(coros_auth: CorosConnect, fit_data: bytes, source_id: str) -> dict:
    """
    内部方法：封装将 FIT ZIP 文件上传到高驰服务器的逻辑。
    包含打包 ZIP、上传 OSS 及调用导入接口。
    """
    # 1. 保存为 ZIP 文件
    os.makedirs(GARMIN_FIT_DIR, exist_ok=True)
    zip_path = os.path.join(GARMIN_FIT_DIR, f"{source_id}.zip")
    with open(zip_path, 'wb') as f:
        f.write(fit_data) 
    
    filesize = os.path.getsize(zip_path)
    md5_hash = calculate_md5_file(zip_path)
    print(f"生成 ZIP 文件: {zip_path}，大小: {filesize}, MD5: {md5_hash}")

    # 2. 上传到 OSS
    oss_path = f"fit_zip/{coros_auth.coros_user_id}/{md5_hash}.zip"
    print(f"准备上传到 OSS，路径: {oss_path}，区域: {coros_auth.region}")
    
    if coros_auth.region == 2:  # 中国区
        oss_client = AliOssClient()
    else:  # 国外/其他
        oss_client = AwsOssClient()
        
    try:
        oss_client.multipart_upload(zip_path, oss_path)
        print(f"成功上传到 OSS: {oss_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传到 OSS 失败: {str(e)}")

    # 3. 调用 Coros uploadActivity 接口
    team_api = get_team_api_base(str(coros_auth.region))
    upload_url = f"{team_api}/activity/fit/import"
    rid = int(coros_auth.region) if coros_auth.region else 1
    sts = STS_CONFIG.get(rid, STS_CONFIG[1])

    params = {
        "source": 1,
        "timezone": 32,
        "bucket": sts["bucket"],
        "md5": md5_hash,
        "size": filesize,
        "object": f"{oss_path}",
        "serviceName": sts["service"],
        "oriFileName": f"{source_id}.zip"
    }
    
    try:
        res = requests.post(
            upload_url,
            headers={
                "accesstoken": coros_auth.access_token,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={"jsonParameter": json.dumps(params)},
            timeout=60
        ).json()
        print(f"高驰 uploadActivity 响应: {json.dumps(res)}")
    except Exception as e:
        print("上传异常:", e)
        return {"status": "error", "message": f"上传异常: {str(e)}"}
        
    if res.get("result") == "0000" and res.get("data", {}).get("status") == 2:        
        return {"status": "success", "message": "已成功同步到高驰", "data": res}
        
    return {"status": "error", "message": f"高驰导入失败: {res.get('message', '未知错误')}", "details": res}


def sync_garmin_to_coros(db: Session, user_id: int, garmin_activity_id: int) -> dict:
    """将佳明活动上传到高驰，支持 OSS + uploadActivity 流程"""
    # 查询 Garmin 活动
    ga = db.query(GarminActivity).filter(
        GarminActivity.user_id == user_id,
        GarminActivity.id == garmin_activity_id
    ).first()
    if not ga or not ga.garmin_connect:
        raise HTTPException(status_code=404, detail="未找到有效的佳明记录或授权")

    # 查询 Coros 授权
    ca = db.query(CorosConnect).filter(
        CorosConnect.user_id == user_id,
        CorosConnect.is_active == True
    ).first()
    if not ca:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权")

    # 下载 Garmin 文件
    base = "connect.garmin.cn" if ga.garmin_connect.region == "CN" else "connect.garmin.com"
    down_url = f"https://{base}/download-service/files/activity/{ga.activity_id}"
    print(f"准备下载 Garmin 活动 {ga.activity_id}，URL: {down_url}")
    headers = {"di-backend": base, "Authorization": f"Bearer {ga.garmin_connect.access_token}"}
    resp = requests.get(down_url, headers=headers, timeout=30)
    print(f"下载佳明活动 {ga.activity_id}，HTTP 状态码: {resp.status_code}")
    file_data = resp.content
    # 校验下载文件大小
    if len(file_data) < 10000:  
        raise HTTPException(
            status_code=400,
            detail=f"下载到的 Garmin 文件可能不完整，大小: {len(file_data)} 字节"
        )
    print(f"成功下载 Garmin 活动 {ga.activity_id}，文件大小: {len(file_data)} 字节")

    return _upload_fit_zip_to_coros(ca, file_data, str(ga.activity_id))