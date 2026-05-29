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
from app.services import base_connect_service, coros_service
from app.utils.crypto_utils import CryptoUtils
from app.utils.logger_utils import log_request

GARMIN_UPLOAD_API_DOMAIN = {
    "CN": "connectapi.garmin.cn",
    "GLOBAL": "connectapi.garmin.com",
}


def get_garmin_connect(connect_id: int, db: Session, current_user: User) -> BaseConnect:
    """获取指定用户的指定的佳明授权配置。"""
    if not connect_id:
        return None
    return (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id, BaseConnect.id == connect_id
        )
        .first()
    )


def get_garmin_configs(db: Session, current_user: User) -> List[BaseConnect]:
    """获取指定用户的所有佳明授权配置。"""
    return (
        db.query(BaseConnect).filter(BaseConnect.user_id == current_user.user_id).all()
    )


def test_garmin_token(id: int, db: Session, current_user: User) -> bool:
    """测试 Token 有效性"""
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.user_id, BaseConnect.id == id)
        .first()
    )
    if not base_connect:
        print(f"NOT FOUND GARMIN CONNECT {id}")
        return False
    try:
        start, limit = 0, 1
        # print(base_connect.region)
        if base_connect.region == "CN" or base_connect.region == "cn":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")
        api_url = "/activitylist-service/activities/search/activities"
        params = {"start": start, "limit": limit}
        response = garth.connectapi(path=api_url, params=params)
        print(f"测试佳明 token 有效性。{response}")
        if response:
            return True
        else:
            return False
    except Exception as e:
        print(f"测试佳明 token 有效性失败: {str(e)}")
        return False


def update_garmin_count(db: Session, garmin_connect_id: int, total_count: int) -> bool:
    """更新 BaseConnect 中对应的 total_count 的值。"""
    garmin_auth = (
        db.query(BaseConnect).filter(BaseConnect.id == garmin_connect_id).first()
    )
    if garmin_auth:
        garmin_auth.total_count = total_count
        db.commit()
        return True
    return False


def save_garmin_connection(
    db: Session,
    user_id: int,
    id: Optional[int] = None,
    token_data: Any = None,
    secret_string: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    region: Optional[str] = None,
) -> BaseConnect:
    """
    支持从 OAuth token 数据 (TokenData) 或 Garth 凭据字符串 (secret_string) 进行更新。
    """
    print(f"开始保存登录信息")

    garmin_guid = None

    # 1. 如果提供了 token_data，优先解析以确定 region 和 guid
    print(f"token_data: {token_data}")
    if token_data:
        try:
            oauth2 = token_data.oauth2
            _, payload_b64, _ = oauth2.access_token.split(".")
            missing_padding = len(payload_b64) % 4
            if missing_padding:
                payload_b64 += "=" * (4 - missing_padding)

            decoded_payload = json.loads(
                base64.urlsafe_b64decode(payload_b64).decode("utf-8")
            )

            # print(f"解析出来的佳明登录信息 {decoded_payload}")

            garmin_guid = decoded_payload.get("garmin_guid")
            iss = decoded_payload.get("iss", "")
            region = "GLOBAL" if "garmin.com" in iss else "CN"
        except Exception as e:
            print(f"解析 Garmin Token 失败: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"解析 Garmin Token 失败: {str(e)}"
            )

    # 2. 定位记录：优先按数据库主键查找，次选业务逻辑查找
    garmin_auth = None
    if id:
        garmin_auth = db.query(BaseConnect).filter(BaseConnect.id == id).first()

    # 3. 如果还是没找到，则新建
    if not garmin_auth:
        print(f"新建 对应 region {region}")
        garmin_auth = BaseConnect(user_id=user_id, region=region)
        db.add(garmin_auth)

    # 4. 统一更新字段
    garmin_auth.is_active = True
    garmin_auth.source_type = "garmin"
    garmin_auth.updated_at = datetime.now(timezone.utc)
    if username:
        garmin_auth.account = username
    if password:
        garmin_auth.encrypted_password = password
    if secret_string:
        garmin_auth.secret_string = secret_string
    if garmin_guid:
        garmin_auth.guid = garmin_guid

    if token_data:
        oauth1 = token_data.oauth1
        oauth2 = token_data.oauth2
        garmin_auth.oauth_token = oauth1.oauth_token
        garmin_auth.oauth_token_secret = oauth1.oauth_token_secret
        garmin_auth.access_token = oauth2.access_token
        garmin_auth.refresh_token = oauth2.refresh_token
        garmin_auth.access_token_expires_at = datetime.fromtimestamp(oauth2.expires_at)
        garmin_auth.refresh_token_expires_at = datetime.fromtimestamp(
            oauth2.refresh_token_expires_at
        )
    db.commit()
    return garmin_auth


def refresh_garmin_secret_string(
    connect_id: int, db: Session, current_user: User
) -> BaseConnect:
    """使用保存的凭据模拟登录并刷新 secret_string。"""
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise HTTPException(status_code=500, detail="SECRET_KEY not configured")

    garmin_connect = get_garmin_connect(connect_id, db, current_user)
    if not garmin_connect:
        raise HTTPException(
            status_code=404, detail="No Garmin configuration found for the user."
        )

    return get_garmin_secret_string(
        id=connect_id,
        account=garmin_connect.account,
        encrypted_password=garmin_connect.encrypted_password,
        region=garmin_connect.region,
        db=db,
        current_user=current_user,
    )


def get_garmin_secret_string(
    id: int,
    account: str,
    encrypted_password: str,
    region: str,
    db: Session,
    current_user: User,
) -> BaseConnect:
    """使用保存的凭据模拟登录并刷新 secret_string。"""
    secret_key = os.getenv("SECRET_KEY")
    try:
        raw_password = CryptoUtils.decrypt(encrypted_password, secret_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密码解密失败: {str(e)}")
    print(f"开始佳明模拟登录，{region}")
    try:
        if region and region.upper() == "CN":
            base_url = "garmin.cn"
            garth.configure(domain=base_url, ssl_verify=False)
        else:
            base_url = "garmin.com"
            garth.configure(domain=base_url)

        print(f"{account} | {raw_password} | {region}")
        with log_request(
            current_user=current_user,
            req_url=base_url + " | garth.login",
            req_method="GET",
            req_params=account,
            log_type="login",
            module_name="garmin",
            op_desc="佳明模拟登录",
        ) as ctx:
            garth.login(account, raw_password)
            secret_string = garth.client.dumps()
            print(f"佳明 { region } 模拟登录成功")
            ctx["response"] = secret_string

    except garth.exc.GarthException as e:
        print(f"登录佳明认证失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"登录佳明认证失败: {str(e)}")
    except Exception as e:
        print(f"佳明连接异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"佳明连接异常: {str(e)}")

    if id and id > 0:
        return save_garmin_connection(
            id=id,
            db=db,
            user_id=current_user.user_id,
            username=account,
            password=encrypted_password,
            secret_string=secret_string,
            region=region,
        )
    else:
        return save_garmin_connection(
            db=db,
            user_id=current_user.user_id,
            username=account,
            password=encrypted_password,
            secret_string=secret_string,
            region=region,
        )


def refresh_garmin_access_token(
    id: int, db: Session, current_user: User
) -> BaseConnect:
    """通过 secret_string 获取有效的 Access Token，必要时自动刷新。"""
    garmin_config = base_connect_service.get_connect(id, db, current_user)

    if not garmin_config or not garmin_config.secret_string:
        raise HTTPException(status_code=404, detail="找不到有效的佳明配置或凭证字符串")

    try:
        garth.client = Client()
        if garmin_config.region and str(garmin_config.region).upper() == "CN":
            garth.configure(domain="garmin.cn", ssl_verify=False)
        else:
            garth.configure(domain="garmin.com")

        # print(f"secret_string = {garmin_config.secret_string}")

        garth.client.loads(garmin_config.secret_string)

        if garth.client.oauth2_token:
            # 刷新 OAuth2 令牌
            garth.client.refresh_oauth2()
            new_secret_string = garth.client.dumps()

            # print(f"garmin 登录成功:{ new_secret_string }")

            try:
                # 1. Base64 解码得到 bytes，再转成 utf-8 字符串
                decoded_bytes = base64.b64decode(new_secret_string)
                decoded_string = decoded_bytes.decode("utf-8")

                # 2. 将解码后的 JSON 字符串解析为原始列表
                secret_data = json.loads(decoded_string)
                # print(f"成功解码原始 token 列表: { secret_data }")

                # 3. 根据 TokenDataHelper 的构造函数，组装其需要的双层字典结构
                token_dict = {}
                oauth1_sub_dict = {}
                oauth2_sub_dict = {}

                if isinstance(secret_data, list):
                    for item in secret_data:
                        if isinstance(item, dict):
                            # 通过核心键分流 OAuth1.0 和 OAuth2.0 凭证
                            if "oauth_token" in item:
                                oauth1_sub_dict.update(item)
                            if "access_token" in item:
                                oauth2_sub_dict.update(item)

                # 满足 TokenDataHelper 内部 data["oauth1"] 和 data["oauth2"] 的取值需求
                token_dict["oauth1"] = oauth1_sub_dict
                token_dict["oauth2"] = oauth2_sub_dict

                # print(f"最终带有命名空间的兼容字典: { token_dict }")

            except Exception as e:
                print(
                    f"Base64解码或JSON解析失败: {str(e)}, 原始数据: {new_secret_string}"
                )
                raise HTTPException(
                    status_code=500, detail=f"解析佳明凭证失败: {str(e)}"
                )

            # 4. 保存连接并返回
            base_connect = save_garmin_connection(
                db=db,
                user_id=current_user.user_id,
                id=garmin_config.id,
                token_data=TokenDataHelper(token_dict),
                username=garmin_config.account,
                password=garmin_config.encrypted_password,
                secret_string=new_secret_string,
            )
            return base_connect

        oauth2_token = garth.client.oauth2_token
        if not oauth2_token or not oauth2_token.access_token:
            raise Exception("佳明 OAuth2 Token 或 access_token 字段为空")

        return None

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        print(f"佳明 Token 处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"佳明 Token 处理失败: {str(e)}")


class TokenDataHelper:
    """临时辅助类，用于将 dict 转换为 save_garmin_auth_config 期望的对象格式。"""

    def __init__(self, data):
        self.oauth1 = type("obj", (object,), data["oauth1"])
        self.oauth2 = type("obj", (object,), data["oauth2"])


def _sync_garmin_activities_internal(
    db: Session,
    config: BaseConnect,
    current_user: User,
    start: int = 0,
    limit: int = 5,
    incremental: bool = True,
) -> Tuple[int, int]:
    """辅助方法：使用 garth 抓取并保存活动。"""

    # 1. 动态配置 garth 的域名（根据 CN 或 国际区）
    if config.region and config.region.upper() == "CN":
        garth.client.configure(domain="garmin.cn", ssl_verify=False)
    else:
        garth.client.configure(domain="garmin.com")

    # 对应原原接口的相对路径
    api_path = "/activitylist-service/activities/search/activities"
    api_params = {"start": start, "limit": limit}

    try:
        # 使用原有的日志记录器，这里将完整的 URL 拼接出来供日志使用
        full_url = f"https://connect.{garth.client.domain}{api_path}"

        with log_request(
            current_user=current_user,
            req_url=full_url,
            req_method="GET",
            req_params=api_params,
            log_type="query",
            module_name="garmin",
            op_desc="获取佳明运动记录",
        ) as ctx:
            # 使用 garth 的内置 client 发送请求，它会自动处理授权 Headers
            response = garth.connectapi(api_path, params=api_params)
            ctx["response"] = response

        # garth 返回的直接就是解析后的 JSON 数据（通常是 list 或 dict）
        activities_data = response
        print(f"获取佳明运动记录，{activities_data}")

    except Exception as e:
        print(f"同步佳明数据失败: {str(e)}")
        raise HTTPException(status_code=400, detail=f"同步佳明数据失败: {str(e)}")

    if not activities_data or not isinstance(activities_data, list):
        return 0, 0

    # 2. 提取 ID 并去重 (统一映射至 Garmin 真实的驼峰命名 'activityId')
    activity_ids = [
        str(item.get("activityId"))
        for item in activities_data
        if item.get("activityId")
    ]

    existing_ids = (
        {
            aid
            for (aid,) in db.query(BaseActivity.activity_id)
            .filter(BaseActivity.activity_id.in_(activity_ids))
            .all()
        }
        if activity_ids
        else set()
    )

    saved_count = 0
    for item in activities_data:
        activity_id = str(item.get("activityId")) if item.get("activityId") else None
        if not activity_id:
            continue

        if activity_id in existing_ids:
            if incremental:
                # 增量同步模式下，遇到已存在记录即停止本批次后续处理
                break
            continue

        # 3. 转换时间（精准匹配 JSON 中的 startTimeGMT, startTimeLocal, endTimeGMT）
        start_time_gmt = None
        start_time_local = None
        end_time_gmt = None

        if item.get("startTimeGMT"):
            start_time_gmt = datetime.fromisoformat(item["startTimeGMT"]).replace(
                tzinfo=timezone.utc
            )

        if item.get("startTimeLocal"):
            start_time_local = datetime.fromisoformat(item["startTimeLocal"])

        if item.get("endTimeGMT"):
            end_time_gmt = datetime.fromisoformat(item["endTimeGMT"]).replace(
                tzinfo=timezone.utc
            )

        # 4. 构建并保存模型（字段无缝对齐最新 PostgreSQL 物理表结构）
        new_activity = BaseActivity(
            # 主键与关联外键
            user_id=current_user.user_id,
            base_connect_id=config.id,
            # 数据来源追踪
            source_type="garmin",
            activity_id=activity_id,
            # 基础信息
            activity_name=item.get("activityName"),
            sport_type_raw=item.get("activityType", {}).get("typeKey"),
            sport_mode_raw=item.get("activityType", {}).get("typeId"),
            # 时间与空间
            start_time_gmt=start_time_gmt,
            start_time_local=start_time_local,
            end_time_gmt=end_time_gmt,
            # 核心运动数据
            distance_meters=item.get("distance"),
            duration_seconds=item.get("duration"),
            moving_duration_seconds=item.get("movingDuration"),
            calories=item.get("calories"),
            # 生理与运动指标
            average_hr=item.get("averageHR"),
            max_hr=item.get("maxHR"),
            average_cadence=item.get("averageRunningCadenceInStepsPerMinute")
            or item.get("averageBikingCadenceInRevPerMinute"),
            average_speed=item.get("averageSpeed"),
            max_speed=item.get("maxSpeed"),
            # 地理位置与设备
            start_lat=item.get("startLatitude"),
            start_lon=item.get("startLongitude"),
            location_name=item.get("locationName"),
            device_id=str(item.get("deviceId")) if item.get("deviceId") else None,
            elevation_gain=item.get("elevationGain"),
            elevation_loss=item.get("elevationLoss"),
        )

        db.add(new_activity)
        saved_count += 1

    return len(activities_data), saved_count


def pull_full_garmin_activities(
    db: Session, current_user: User, connect_id: int, incremental: bool = True
) -> dict:
    """全量或者增量同步佳明活动。"""
    config = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id,
            BaseConnect.id == connect_id,
        )
        .first()
    )
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    start, limit, total_saved, total_fetched = 0, 100, 0, 0
    while True:
        fetched, saved = _sync_garmin_activities_internal(
            db, config, current_user, start, limit, incremental
        )
        total_fetched += fetched
        total_saved += saved
        if fetched < limit:
            break

        # 如果是增量同步且本页保存数量小于获取数量，说明遇到了重复数据，停止分页获取
        if incremental and saved < fetched:
            break
        start += limit

    if total_fetched:
        update_garmin_count(db, config.id, total_fetched)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "status": "success",
        "fetched_count": total_fetched,
        "saved_count": total_saved,
    }


def sync_new_garmin_activities(
    db: Session, user_id: int, region: str, limit: int = 10
) -> dict:
    """增量同步最新佳明活动。"""
    user = db.query(User).filter(User.user_id == user_id).first()
    config = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == user_id, BaseConnect.region == region.upper())
        .first()
    )
    if not config or not config.access_token:
        raise HTTPException(status_code=404, detail="未找到有效的 Garmin 授权配置")

    fetched, saved = _sync_garmin_activities_internal(db, config, user, 0, limit)
    config.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "fetched_count": fetched, "saved_count": saved}


def get_garmin_activity_download_info(
    db: Session, current_user: User, activity_id: int
) -> Tuple[requests.Response, str]:
    """获取佳明文件下载响应对象（不直接读取内容）。"""
    ga = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.user_id, BaseActivity.id == activity_id
        )
        .first()
    )

    if not ga:
        raise HTTPException(status_code=404, detail="未找到活动记录")

    # 根据活动记录寻找对应的 Garmin 配置
    region = "CN" if ga.garmin_cn_activity_id else "GLOBAL"
    config = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id, BaseConnect.region == region
        )
        .first()
    )

    if not config:
        raise HTTPException(status_code=404, detail="未找到有效的佳明授权或活动记录")

    base = "connect.garmin.cn" if region == "CN" else "connect.garmin.com"
    down_url = f"https://{base}/download-service/files/activity/{ga.activity_id}"
    headers = {
        "di-backend": base,
        "Authorization": f"Bearer {config.access_token}",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        # 注意：这里不使用 with 语句，也不手动读取 .content
        # stream=True 允许我们后续分块读取
        resp = requests.get(down_url, headers=headers, timeout=30, stream=True)

        if resp.status_code != 200:
            resp.close()  # 只有在失败时立即关闭
            raise HTTPException(status_code=resp.status_code, detail="佳明文件下载失败")

        return resp, f"{ga.activity_id}.zip"

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"网络请求错误: {str(e)}")


def parse_garmin_upload_response(
    response: requests.Response,
) -> Tuple[str, Optional[dict]]:
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
            msg = (
                result.get("detailedImportResult", {})
                .get("failures", [{}])[0]
                .get("messages", [{}])[0]
                .get("content")
            )
            if msg == "Duplicate Activity.":
                return "DUPLICATE_ACTIVITY", result
        except:
            pass
        return "UPLOAD_CONFLICT", result

    return "UPLOAD_FAILED" if result else "UPLOAD_EXCEPTION", result


def _upload_file_to_garmin(
    current_user: User,
    target_config: BaseConnect,
    file_data: bytes,
    filename: str,
    op_desc: str,
) -> dict:
    """内部辅助方法：执行将文件上传到佳明服务器的通用逻辑。支持自动解压 zip 中的 fit 文件。"""

    # --- 新增逻辑：处理 ZIP 文件 ---
    upload_data = file_data
    upload_filename = filename

    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(file_data)) as z:
                # 获取 zip 中所有后缀为 .fit 的文件列表
                fit_files = [f for f in z.namelist() if f.lower().endswith(".fit")]

                if not fit_files:
                    return {
                        "status": "error",
                        "message": "Zip 压缩包中未找到 .fit 文件",
                    }

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
        "di-backend": (
            "connect.garmin.cn" if target_region == "CN" else "connect.garmin.com"
        ),
    }
    url = f"https://{api_domain}/upload-service/upload"

    with log_request(
        current_user=current_user,
        req_url=url,
        req_method="POST",
        req_params=None,
        log_type="upload",
        module_name="garmin",
        op_desc=op_desc,
    ) as ctx:
        # 使用处理后的 upload_filename 和 upload_data
        resp = requests.post(
            url,
            headers=headers,
            files={"file": (upload_filename, upload_data, "application/octet-stream")},
            timeout=60,
        )
        ctx["response"] = resp

    status, json_res = parse_garmin_upload_response(resp)
    return {
        "status": "success",
        "upload_status": status,
        "target_region": target_region,
        "http_status": resp.status_code,
        "garmin_response": json_res,
        "actual_filename": upload_filename,  # 可选：记录实际上传的文件名
    }


def sync_garmin_to_garmin(db: Session, current_user: User, activity_id: int) -> dict:
    """佳明之间同步逻辑。"""
    ga = (
        db.query(BaseActivity)
        .filter(
            BaseActivity.user_id == current_user.user_id, BaseActivity.id == activity_id
        )
        .first()
    )
    if not ga:
        raise HTTPException(status_code=404, detail="记录不存在")

    source_region = "CN" if ga.garmin_cn_activity_id else "GLOBAL"
    target_region = "GLOBAL" if source_region == "CN" else "CN"

    target_config = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id,
            BaseConnect.region == target_region,
        )
        .first()
    )
    if not target_config:
        raise HTTPException(status_code=404, detail="目标区域未授权")

    file_resp, filename = get_garmin_activity_download_info(
        db, current_user, activity_id
    )
    return _upload_file_to_garmin(
        current_user, target_config, file_resp.content, filename, "佳明上传运动"
    )


def sync_coros_to_garmin(
    db: Session, current_user: User, base_activity_id: int, target_region: str = "CN"
) -> dict:
    """高驰同步到佳明逻辑。"""
    target_config = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.user_id,
            BaseConnect.region == target_region,
        )
        .first()
    )
    if not target_config:
        raise HTTPException(
            status_code=404, detail=f"目标佳明区域 {target_region} 未授权"
        )

    file_resp, filename = coros_service.get_coros_activity_download_info(
        db, current_user, base_activity_id
    )

    return _upload_file_to_garmin(
        current_user, target_config, file_resp.content, filename, "上传佳明活动"
    )


def refresh_garmin_activity_count(db: Session) -> dict:
    """刷新所有用户的佳明活动总数统计。"""
    users = db.query(BaseConnect.user_id).distinct().all()
    for (user_id,) in users:
        garmin_auths = (
            db.query(BaseConnect).filter(BaseConnect.user_id == user_id).all()
        )
        for garmin_auth in garmin_auths:
            # 统计属于该 provider 的活动
            query = db.query(BaseActivity).filter(
                BaseActivity.user_id == user_id,
                BaseActivity.source_provider == "garmin",
            )
            if garmin_auth.region == "CN":
                query = query.filter(BaseActivity.garmin_cn_activity_id.isnot(None))
            else:
                query = query.filter(BaseActivity.garmin_activity_id.isnot(None))

            activity_count = query.count()
            update_garmin_count(db, garmin_auth.id, activity_count)
