import os
import json
import requests
from datetime import datetime, timezone
from typing import Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.base_connect import BaseConnect
from app.models.base_activity import BaseActivity
from app.models.user import User
from app.services.oss.ali_oss_client import AliOssClient
from app.services.oss.aws_oss_client import AwsOssClient
from app.utils.coros_region_config import REGIONCONFIG
from app.utils.coros_sts_config import STS_CONFIG
from app.utils.md5_utils import calculate_md5_file
from app.utils.config import GARMIN_FIT_DIR
from app.utils.logger_utils import log_operation_async, log_request


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
    coros_auth = (
        db.query(BaseConnect).filter(BaseConnect.id == coros_connect_id).first()
    )
    if coros_auth:
        coros_auth.total_count = total_count
        db.commit()
        return True
    return False


def test_coros_token(connect_id: int, db: Session, current_user: User) -> bool:
    """测试 Token 有效性"""
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == connect_id)
        .first()
    )
    if not base_connect:
        print(f"NOT FOUND COROS CONNECT {connect_id}")
        return False
    base_url = get_team_api_base(str(base_connect.region))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "accesstoken": base_connect.access_token,
    }
    page_size = 1
    page_number = 1
    query_url = f"{base_url}/activity/query?size={page_size}&pageNumber={page_number}"
    response = requests.get(query_url, headers=headers, timeout=10)
    print(f"测试coros token 有效性。{json.dumps(response.json())}")
    try:
        data_json = response.json()
    except ValueError:
        return False


def is_response_valid(response) -> bool:
    # 1. 检查 HTTP 状态码
    if response.status_code != 200:
        return False

    # 2. 获取 JSON 数据
    data_json = response.json()

    # 3. 校验业务逻辑核心字段
    # 判断 result 是否为 "0000" (通常代表业务成功)
    # 并且确保 dataList 存在且不是 None
    result_code = data_json.get("result")
    data_content = data_json.get("data") or {}
    data_list = data_content.get("dataList")

    return result_code == "0000" and data_list is not None


def perform_coros_login(
    id: int, account: str, encrypted_password: str, db: Session, current_user: User
) -> BaseConnect:
    """执行高驰登录逻辑并更新授权信息。"""
    coros_auth = None
    if id:
        coros_auth = (
            db.query(BaseConnect)
            .filter(
                BaseConnect.user_id == current_user.id,
                BaseConnect.id == id,
            )
            .first()
        )

    login_url = "https://teamcnapi.coros.com/account/login"
    login_data = {
        "account": account,
        "pwd": encrypted_password,
        "accountType": 2,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.39 Safari/537.36",
    }

    try:
        with log_request(
            current_user=current_user,
            req_url=login_url,
            req_method="POST",
            req_params=login_data,
            log_type="login",
            module_name="coros",
            op_desc="高驰模拟登录",
        ) as ctx:
            response = requests.post(
                login_url, json=login_data, headers=headers, timeout=10
            )
            ctx["response"] = response
        response.raise_for_status()
        login_response = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"登录失败: {str(e)}")

    if login_response.get("result") != "0000":
        if coros_auth:
            coros_auth.is_active = False
            db.commit()
        raise HTTPException(
            status_code=400, detail=f"高驰登录失败: {login_response.get('message')}"
        )

    data = login_response.get("data", {})
    print(data)
    if not coros_auth:
        coros_auth = BaseConnect(user_id=current_user.id)
        db.add(coros_auth)

    coros_auth.source_type = "coros"
    coros_auth.account = account
    coros_auth.encrypted_password = encrypted_password
    coros_auth.access_token = data.get("accessToken")
    coros_auth.user_id = current_user.id
    coros_auth.guid = str(data.get("userId"))
    coros_auth.region = data.get("regionId")
    coros_auth.is_active = True
    coros_auth.updated_at = datetime.now(timezone.utc)
    db.commit()
    return coros_auth


def pull_full_coros_activities(
    db: Session, current_user: User, connect_id: int, incremental: bool = True
) -> dict:
    """同步高驰运动记录。incremental 表示是否增量拉取。"""
    base_connect = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.is_active == True,
            BaseConnect.id == connect_id,
        )
        .first()
    )

    if not base_connect or not base_connect.access_token:
        raise HTTPException(
            status_code=404, detail="未找到有效的高驰授权配置，请先绑定账号"
        )

    # TODO 测试 token 有效性，如果无效则刷新 token

    if not test_coros_token(base_connect.id, db, current_user):
        base_connect = perform_coros_login(
            base_connect.id,
            base_connect.account,
            base_connect.encrypted_password,
            db,
            current_user,
        )
    base_url = get_team_api_base(str(base_connect.region))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "accesstoken": base_connect.access_token,
    }

    page_size = 50
    page_number = 1

    total_count = 0
    total_fetched = 0
    new_saved_count = 0
    stop_fetching = False

    while True:
        query_url = (
            f"{base_url}/activity/query?size={page_size}&pageNumber={page_number}"
        )
        try:
            with log_request(
                current_user=current_user,
                req_url=query_url,
                req_method="GET",
                req_params=None,
                log_type="query",
                module_name="coros",
                op_desc="获取高驰运动记录",
            ) as ctx:
                response = requests.get(query_url, headers=headers, timeout=10)
                ctx["response"] = response

            response.raise_for_status()
            result = response.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"获取高驰数据失败: {str(e)}")

        if result.get("result") != "0000":
            raise HTTPException(
                status_code=400, detail=f"高驰 API 返回异常: {result.get('message')}"
            )

        data = result.get("data", {})
        if page_number == 1:
            total_count = data.get("count", 0)
            update_coros_count(db, base_connect.id, total_count)

        activities_list = data.get("dataList", [])
        if not activities_list:
            break

        # 批量查重
        label_ids = [
            str(item.get("labelId")) for item in activities_list if item.get("labelId")
        ]
        existing_ids = set()
        if label_ids:
            existing_ids = {
                lid
                for (lid,) in db.query(BaseActivity.activity_id)
                .filter(BaseActivity.activity_id.in_(label_ids))
                .all()
            }

        for item in activities_list:
            label_id = str(item.get("labelId"))
            if label_id in existing_ids:
                # 如果是增量拉取则停止
                if incremental:
                    stop_fetching = True
                    break
                continue
            # 时间处理
            start_ts = item.get("startTime")
            end_ts = item.get("endTime")
            start_dt_gmt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            end_dt_gmt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            start_dt_local = datetime.fromtimestamp(start_ts)
            new_activity = BaseActivity(
                base_connect_id=base_connect.id,
                user_id=current_user.id,
                source_type="coros",
                activity_id=str(item.get("labelId")),
                activity_name=item.get("name"),
                sport_type_raw=item.get("sportType"),
                sport_mode_raw=item.get("mode"),
                start_time_gmt=start_dt_gmt,
                start_time_local=start_dt_local,
                end_time_gmt=end_dt_gmt,
                distance_meters=item.get("distance"),
                duration_seconds=item.get("totalTime"),
                moving_duration_seconds=item.get("movingTime"),
                calories=item.get("calorie"),
                average_hr=item.get("avgHr"),
                max_hr=item.get("maxHr"),
                elevation_gain=item.get("ascent"),
                elevation_loss=item.get("descent"),
            )
            db.add(new_activity)
            new_saved_count += 1

        total_fetched += len(activities_list)
        if (
            stop_fetching
            or total_fetched >= total_count
            or len(activities_list) < page_size
        ):
            break
        page_number += 1

    base_connect.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success",
        "total_in_platform": total_count,
        "fetched_count": total_fetched,
        "new_saved_count": new_saved_count,
    }


def get_coros_activity_download_info(
    db: Session, current_user: User, connect_id: int, activity_id: int
) -> Tuple[requests.Response, str]:
    """获取高驰运动记录的文件流及建议的文件名。"""
    coros_auth = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.is_active == True,
            BaseConnect.id == connect_id,
        )
        .first()
    )
    if not coros_auth:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权配置")

    activity = (
        db.query(BaseActivity)
        .filter(BaseActivity.user_id == current_user.id, BaseActivity.id == activity_id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="未找到运动记录")

    base_url = get_team_api_base(str(coros_auth.region))
    meta_url = f"{base_url}/activity/detail/download?labelId={activity.activity_id}&sportType={activity.sport_type_raw}&fileType=4"
    headers = {"accesstoken": coros_auth.access_token}

    with log_request(
        current_user=current_user,
        req_url=meta_url,
        req_method="POST",
        req_params=None,
        log_type="download",
        module_name="coros",
        op_desc="高驰获取下载运动链接",
    ) as ctx:
        meta_res = requests.post(meta_url, headers=headers, timeout=10).json()
        ctx["response"] = meta_res

    if meta_res.get("result") != "0000":
        raise HTTPException(
            status_code=400, detail=f"获取下载链接失败: {meta_res.get('message')}"
        )

    download_url = meta_res.get("data", {}).get("fileUrl")
    with log_request(
        current_user=current_user,
        req_url=download_url,
        req_method="GET",
        req_params=None,
        log_type="fileUrl",
        module_name="coros",
        op_desc="下载高驰运动文件",
    ) as ctx:
        file_response = requests.get(download_url, stream=True, timeout=30)
        ctx["response"] = None
    log_operation_async(
        user_id=current_user.id,
        log_type="DOWNLOAD",
        module_name="coros",
        op_desc="下载高驰运动文件",
    )
    file_response.raise_for_status()
    return file_response, f"coros_activity_{activity.activity_id}.fit"


def _upload_fit_zip_to_coros(
    current_user: User, coros_auth: BaseConnect, fit_data: bytes, source_id: str
) -> dict:
    """
    内部方法：封装将 FIT ZIP 文件上传到高驰服务器的逻辑。
    包含打包 ZIP、上传 OSS 及调用导入接口。
    """
    # 1. 保存为 ZIP 文件
    os.makedirs(GARMIN_FIT_DIR, exist_ok=True)
    zip_path = os.path.join(GARMIN_FIT_DIR, f"{source_id}.zip")
    with open(zip_path, "wb") as f:
        f.write(fit_data)

    filesize = os.path.getsize(zip_path)
    md5_hash = calculate_md5_file(zip_path)
    # print(f"生成 ZIP 文件: {zip_path}，大小: {filesize}, MD5: {md5_hash}")

    # 2. 上传到 OSS
    oss_path = f"fit_zip/{coros_auth.guid}/{md5_hash}.zip"
    # print(f"准备上传到 OSS，路径: {oss_path}，区域: {coros_auth.region}")

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
        "oriFileName": f"{source_id}.zip",
    }
    try:
        with log_request(
            current_user=current_user,
            req_url=upload_url,
            req_method="POST",
            req_params=params,
            log_type="upload",
            module_name="coros",
            op_desc="高驰上传运动文件",
        ) as ctx:
            res = requests.post(
                upload_url,
                headers={
                    "accesstoken": coros_auth.access_token,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"jsonParameter": json.dumps(params)},
                timeout=60,
            ).json()
            ctx["response"] = res
        # print(f"高驰 uploadActivity 响应: {json.dumps(res)}")
    except Exception as e:
        # print("上传异常:", e)
        return {"status": "error", "message": f"上传异常: {str(e)}"}
    if res.get("result") == "0000" and res.get("data", {}).get("status") == 2:
        log_operation_async(
            user_id=current_user.id,
            log_type="UPLOAD",
            module_name="coros",
            op_desc="上传活动到高驰",
        )
        return {"status": "success", "message": "已成功同步到高驰", "data": res}
    return {
        "status": "error",
        "message": f"高驰导入失败: {res.get('message', '未知错误')}",
        "details": res,
    }


def sync_garmin_to_coros(
    db: Session, current_user: User, garmin_activity_id: int, connect_id: int
) -> dict:
    """将佳明活动上传到高驰，支持 OSS + uploadActivity 流程"""
    # 查询 Garmin 活动
    ga = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.id,
            BaseActivity.id == garmin_activity_id,
        )
        .first()
    )
    if not ga or not ga.garmin_connect:
        raise HTTPException(status_code=404, detail="未找到有效的佳明记录或授权")

    # 查询 Coros 授权
    ca = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.is_active == True,
            BaseConnect.id == connect_id,
        )
        .first()
    )
    if not ca:
        raise HTTPException(status_code=404, detail="未找到有效的高驰授权")

    # 下载 Garmin 文件
    if ga.garmin_connect.region == "CN" or ga.garmin_connect.region == "cn":
        base = "connect.garmin.cn"
    else:
        base = "connect.garmin.com"
    down_url = f"https://{base}/download-service/files/activity/{ga.activity_id}"
    # print(f"准备下载 Garmin 活动 {ga.activity_id}，URL: {down_url}")
    headers = {
        "di-backend": base,
        "Authorization": f"Bearer {ga.garmin_connect.access_token}",
    }
    with log_request(
        current_user=current_user,
        req_url=down_url,
        req_method="GET",
        req_params=None,
        log_type="fileUrl",
        module_name="garmin",
        op_desc="佳明下载运动文件zip",
    ) as ctx:
        resp = requests.get(down_url, headers=headers, timeout=30)
        ctx["response"] = None

    # print(f"下载佳明活动 {ga.activity_id}，HTTP 状态码: {resp.status_code}")
    file_data = resp.content
    # 校验下载文件大小
    # if len(file_data) < 10000:
    #     raise HTTPException(
    #         status_code=400,
    #         detail=f"下载到的 Garmin 文件可能不完整，大小: {len(file_data)} 字节"
    #     )
    print(f"成功下载 Garmin 活动 {ga.activity_id}，文件大小: {len(file_data)} 字节")

    return _upload_fit_zip_to_coros(current_user, ca, file_data, str(ga.activity_id))
