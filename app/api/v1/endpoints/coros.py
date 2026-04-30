import hashlib
import requests
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models.user import User
from app.models.coros_connect import CorosConnect
from app.models.coros_activity import CorosActivity
from app.core.security import get_current_user

router = APIRouter()

# --- 辅助工具 ---

def get_team_api_base(region_id: str) -> str:
    """
    根据区域 ID 返回对应的 API 域名
    1: 中国区 (CN), 其他通常为国际区
    """
    if region_id == "1":
        return "https://teamcnapi.coros.com"
    return "https://teamapi.coros.com"

# --- 定义前端请求的数据结构 ---

class CorosLoginRequest(BaseModel):
    email: str
    password: str

@router.post("/login")
def login_coros(
    payload: CorosLoginRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    模拟高驰 (Coros) 登录并将认证信息存入数据库。
    参考 CorosClient 逻辑实现。
    """
    # 1. 执行模拟登录
    # 目前默认使用中国区登录地址
    login_url = "https://teamcnapi.coros.com/account/login"
    
    # 密码需要 MD5 加密
    hashed_password = hashlib.md5(payload.password.encode()).hexdigest()
    
    login_data = {
        "account": payload.email,
        "pwd": hashed_password,
        "accountType": 2,
    }
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.39 Safari/537.36",
        "referer": "https://teamcnapi.coros.com/",
        "origin": "https://teamcnapi.coros.com/",
    }

    try:
        response = requests.post(login_url, json=login_data, headers=headers, timeout=10)
        response.raise_for_status()
        login_response = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"请求 Coros 接口失败: {str(e)}")

    if login_response.get("result") != "0000":
        raise HTTPException(status_code=401, detail=f"高驰登录失败: {login_response.get('message')}")

    data = login_response.get("data", {})
    access_token = data.get("accessToken")
    coros_user_id = data.get("userId")
    region_id = data.get("regionId")

    # 2. 保存或更新数据库配置
    coros_auth = db.query(CorosConnect).filter(
        CorosConnect.user_id == current_user.user_id
    ).first()

    if not coros_auth:
        coros_auth = CorosConnect(user_id=current_user.user_id)
        db.add(coros_auth)

    coros_auth.coros_account = payload.email
    # TODO：建议对密码进行加密后再存储，此处遵循项目中 garmin 的处理方式
    coros_auth.coros_password = payload.password 
    coros_auth.access_token = access_token
    coros_auth.coros_user_id = str(coros_user_id)
    coros_auth.region_id = str(region_id)
    coros_auth.is_active = True

    db.commit()

    return {
        "status": "success",
        "data": {
            "coros_user_id": coros_user_id,
            "region_id": region_id
        }
    }

@router.get("/refreshToken")
def refresh_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    刷新 Coros 的访问令牌。
    由于使用模拟登录方式，刷新逻辑可以通过重新调用 login 流程实现。
    """
    return {"status": "success", "message": "Simulated refresh via re-login"}

@router.get("/saveActivities")
def save_coros_activities(
    count: int = Query(10, gt=0, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取高驰运动记录并保存到数据库。
    """
    # 1. 获取授权配置
    coros_auth = db.query(CorosConnect).filter(
        CorosConnect.user_id == current_user.user_id,
        CorosConnect.is_active == True
    ).first()

    if not coros_auth or not coros_auth.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权配置，请先登录")

    # 2. 调用高驰接口获取列表
    base_url = get_team_api_base(coros_auth.region_id)
    # size 设置为用户请求的条数
    query_url = f"{base_url}/activity/query?size={count}&pageNumber=1"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "accesstoken": coros_auth.access_token,
    }

    try:
        response = requests.get(query_url, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"获取高驰数据失败: {str(e)}")

    if result.get("result") != "0000":
        raise HTTPException(status_code=400, detail=f"高驰 API 返回异常: {result.get('message')}")

    activities_list = result.get("data", {}).get("dataList", [])
    
    saved_count = 0
    for item in activities_list:
        label_id = str(item.get("labelId"))
        
        # 检查是否已存在
        existing = db.query(CorosActivity).filter(CorosActivity.label_id == label_id).first()
        if existing:
            continue

        # 转换时间戳 (高驰返回的是秒级时间戳)
        start_dt = datetime.fromtimestamp(item.get("startTime", 0), tz=timezone.utc)
        end_dt = datetime.fromtimestamp(item.get("endTime", 0), tz=timezone.utc)

        new_activity = CorosActivity(
            user_id=current_user.user_id,
            coros_connect_id=coros_auth.id,
            label_id=label_id,
            name=item.get("name"),
            sport_type=item.get("sportType"),
            mode=item.get("mode"),
            distance=item.get("distance"),
            duration=item.get("duration"),
            calories=item.get("calorie"),
            avg_hr=item.get("avgHr"),
            max_hr=item.get("maxHr"),
            start_time=start_dt,
            end_time=end_dt
        )
        db.add(new_activity)
        saved_count += 1

    coros_auth.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "success", "fetched": len(activities_list), "new_saved": saved_count}