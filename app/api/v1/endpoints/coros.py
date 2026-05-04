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
from app.utils.coros_region_config import REGIONCONFIG

router = APIRouter()

# --- 辅助工具 ---

def get_team_api_base(region_id: str) -> str:
    """
    根据区域 ID 从 REGIONCONFIG 获取对应的 API 域名。
    """
    try:
        rid = int(region_id)
        if rid in REGIONCONFIG:
            return REGIONCONFIG[rid]["teamapi"]
    except (ValueError, TypeError):
        pass
    
    # 默认返回国际区 (Region 1)
    return REGIONCONFIG.get(1, {}).get("teamapi", "https://teamapi.coros.com")

def _perform_coros_login_and_update(
    db: Session, 
    user_id: int, 
    account: str, 
    password_encrypted: str, 
    is_refresh: bool = False
):
    """执行高驰登录并更新数据库中的授权信息。"""
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
        "referer": "https://teamcnapi.coros.com/",
        "origin": "https://teamcnapi.coros.com/",
    }

    try:
        response = requests.post(login_url, json=login_data, headers=headers, timeout=10)
        response.raise_for_status()
        login_response = response.json()
    except Exception as e:
        error_msg = f"{'刷新' if is_refresh else '登录'}失败: {str(e)}"
        raise HTTPException(status_code=400, detail=error_msg)

    if login_response.get("result") != "0000":
        if is_refresh and coros_auth:
            coros_auth.is_active = False
            db.commit()
        raise HTTPException(status_code=401, detail=f"高驰登录失败: {login_response.get('message')}")

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
    coros_auth = _perform_coros_login_and_update(
        db=db,
        user_id=current_user.user_id,
        account=payload.email,
        password_encrypted=payload.password,
        is_refresh=False
    )
    
    return {
        "status": "success",
        "data": {
            "coros_user_id": coros_auth.coros_user_id,
            "region_id": coros_auth.region
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
    coros_auth = db.query(CorosConnect).filter(
        CorosConnect.user_id == current_user.user_id
    ).first()

    if not coros_auth or not coros_auth.coros_account or not coros_auth.coros_password_encrypted:
        raise HTTPException(status_code=400, detail="未找到保存的高驰凭据，请重新登录")

    # 统一使用辅助方法进行登录/刷新逻辑，避免重复代码
    coros_auth = _perform_coros_login_and_update(
        db=db,
        user_id=current_user.user_id,
        account=coros_auth.coros_account,
        password_encrypted=coros_auth.coros_password_encrypted,
        is_refresh=True
    )

    return {
        "status": "success",
        "data": {
            "coros_user_id": coros_auth.coros_user_id,
            "region_id": coros_auth.region
        }
    }

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
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权配置，请先绑定账号")

    # 2. 调用高驰接口获取列表
    base_url = get_team_api_base(str(coros_auth.region))
    # size 设置为用户请求的条数
    query_url = f"{base_url}/activity/query?size={count}&pageNumber=1"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "accesstoken": coros_auth.access_token,
    }
    print(query_url);
    print(headers);
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
        start_ts = item.get("startTime", 0)
        end_ts = item.get("endTime", 0)
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc) if start_ts else None
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc) if end_ts else None

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