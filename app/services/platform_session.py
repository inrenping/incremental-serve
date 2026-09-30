import io
import json
import os
import zipfile
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

# 关闭 garth 遥测，避免无关日志噪音
os.environ.setdefault("GARTH_TELEMETRY_ENABLED", "false")

from garth.http import Client as GarminClient  # noqa: E402

from app.models.base_connect import BaseConnect  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.oss.ali_oss_client import AliOssClient  # noqa: E402
from app.services.oss.aws_oss_client import AwsOssClient  # noqa: E402
from app.utils.config import GARMIN_FIT_DIR  # noqa: E402
from app.utils.coros_region_config import REGIONCONFIG  # noqa: E402
from app.utils.coros_sts_config import STS_CONFIG  # noqa: E402
from app.utils.logger_utils import log_request, log_operation_async  # noqa: E402
from app.utils.md5_utils import calculate_md5_file  # noqa: E402

MAX_WORKERS = 3  # 下载/上传并发上限，低于旧实现（5），降低平台风控概率


def build_session(connect: BaseConnect, db: Session, current_user: User):
    """按连接类型构造独立会话对象。"""
    source_type = connect.source_type or ""
    if source_type == "coros":
        return CorosSession(connect, db, current_user)
    if source_type.startswith("garmin"):
        return GarminSession(connect, db, current_user)
    raise ValueError(f"不支持的平台类型: {source_type}")


class GarminSession:
    """单个 Garmin 连接的独立会话。

    关键：不复用模块级 garth.client 单例，每个连接一个 GarthClient 实例，
    持有自己的 OAuth 凭证，避免多连接/多用户并发时凭证互相覆盖。
    """

    def __init__(self, connect: BaseConnect, db: Session, current_user: User):
        self.connect = connect
        self.db = db
        self.current_user = current_user
        self.client = GarminClient()
        self._configure()
        self.client.loads(connect.secret_string)

    def _configure(self):
        region = (self.connect.region or "").upper()
        domain = "garmin.cn" if region == "CN" else "garmin.com"
        self.client.configure(domain=domain, ssl_verify=(domain == "garmin.cn"))

    def _refresh(self):
        """乐观刷新：OAuth2 令牌续期并回写 secret_string。

        注意：本方法可能在线程池 worker 内被 download_fit/upload_fit 的异常重试
        路径调用，因此禁止直接写主请求的 Session（SQLAlchemy Session 非线程安全）。
        这里用独立短生命周期 Session 落库，避免污染 self.db。
        """
        from app.db.session import SessionLocal

        self.client.refresh_oauth2()
        new_secret = self.client.dumps()
        with SessionLocal() as s:
            conn = s.get(BaseConnect, self.connect.id)
            if conn is not None:
                conn.secret_string = new_secret
                s.commit()

    def list_activities(self, count: int) -> list[dict]:
        api_path = "/activitylist-service/activities/search/activities"
        try:
            data = self.client.connectapi(api_path, params={"start": 0, "limit": count})
        except Exception:
            self._refresh()
            data = self.client.connectapi(api_path, params={"start": 0, "limit": count})
        if not data or not isinstance(data, list):
            return []
        activities = []
        for item in data:
            activity_id = str(item.get("activityId")) if item.get("activityId") else None
            if not activity_id:
                continue
            start_gmt = None
            start_local = None
            if item.get("startTimeGMT"):
                start_gmt = datetime.fromisoformat(item["startTimeGMT"]).replace(
                    tzinfo=timezone.utc
                )
            if item.get("startTimeLocal"):
                start_local = datetime.fromisoformat(item["startTimeLocal"])
            activities.append(
                {
                    "activity_id": activity_id,
                    "source_type": "garmin",
                    "activity_name": item.get("activityName") or "",
                    "sport_type_raw": (item.get("activityType") or {}).get("typeKey"),
                    "location_name": item.get("locationName"),
                    "start_time_gmt": start_gmt,
                    "start_time_local": start_local,
                    "distance_meters": item.get("distance"),
                    "_raw": item,
                }
            )
        return activities

    def download_fit(self, activity: dict) -> tuple[bytes, str]:
        url = f"/download-service/files/activity/{activity['activity_id']}"
        try:
            raw = self.client.download(url)
        except Exception:
            self._refresh()
            raw = self.client.download(url)
        if raw.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                fit_names = [n for n in zf.namelist() if n.endswith(".fit")]
                if fit_names:
                    raw = zf.read(fit_names[0])
        return raw, f"{activity['activity_id']}.fit"

    def upload_fit(self, file_data: bytes, filename: str) -> dict:
        """将 FIT 推送到本会话持有的 Garmin 账号。"""
        domain = "garmin.cn" if (self.connect.region or "").upper() == "CN" else "garmin.com"
        self.client.configure(domain=domain, ssl_verify=(domain == "garmin.cn"))
        try:
            upload_url = f"https://connectapi.{domain}/upload-service/upload"
            files = {"file": (filename, file_data, "text/plain")}
            headers = {"Authorization": str(self.client.oauth2_token)}
            resp = requests.post(upload_url, headers=headers, files=files)
            result = resp.json()
            import_result = result.get("detailedImportResult", {})
            if resp.status_code == 202 and import_result.get("uploadId"):
                return {"status": "SUCCESS", "uploadId": import_result.get("uploadId")}
            failures = import_result.get("failures", [])
            if resp.status_code == 409 and "Duplicate" in str(failures[0] if failures else ""):
                return {"status": "DUPLICATE_ACTIVITY", "message": "DUPLICATE_ACTIVITY"}
            return {"status": "UPLOAD_FAILED", "message": str(result)}
        except Exception as e:
            return {"status": "UPLOAD_EXCEPTION", "message": str(e)}


class CorosSession:
    """单个 Coros 连接的独立会话。

    说明：向 Coros 推送时必须经过 Coros 自有的对象存储中转
    （region==2 走阿里云 coros-oss 桶，否则走 AWS service 桶），
    由 Coros 服务端回拉。这是 Coros 协议的一部分，与本项目自有存储
    （Supabase）无关，请勿替换为 Supabase 桶。
    """

    def __init__(self, connect: BaseConnect, db: Session, current_user: User):
        self.connect = connect
        self.db = db
        self.current_user = current_user

    def _base_url(self) -> str:
        try:
            rid = int(self.connect.region)
            if rid in REGIONCONFIG:
                return REGIONCONFIG[rid]["teamapi"]
        except (ValueError, TypeError):
            pass
        return REGIONCONFIG.get(1, {}).get("teamapi", "https://teamapi.coros.com")

    def _headers(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "accesstoken": self.connect.access_token,
        }

    def _refresh(self):
        """Coros 令牌失效时，用保存的账号密码重新登录换取新 access_token。

        同样可能在线程池 worker 内被调用，故用独立 Session 落库（见 GarminSession._refresh）。
        """
        from app.db.session import SessionLocal
        from app.services import coros_service

        with SessionLocal() as s:
            self.connect = coros_service.perform_coros_login(
                id=self.connect.id,
                account=self.connect.account,
                encrypted_password=self.connect.encrypted_password,
                db=s,
                current_user=self.current_user,
            )

    def list_activities(self, count: int) -> list[dict]:
        query_url = f"{self._base_url()}/activity/query?size={count}&pageNumber=1"
        try:
            resp = requests.get(query_url, headers=self._headers(), timeout=10)
            resp.raise_for_status()
            result = resp.json()
        except Exception:
            self._refresh()
            resp = requests.get(query_url, headers=self._headers(), timeout=10)
            resp.raise_for_status()
            result = resp.json()
        if result.get("result") != "0000":
            raise ValueError(f"高驰 API 返回异常: {result.get('message')}")
        activities = []
        for item in (result.get("data") or {}).get("dataList") or []:
            label_id = str(item.get("labelId")) if item.get("labelId") else None
            if not label_id:
                continue
            start_ts = item.get("startTime")
            activities.append(
                {
                    "activity_id": label_id,
                    "source_type": "coros",
                    "activity_name": item.get("name") or "",
                    "sport_type_raw": item.get("sportType"),
                    "start_time_gmt": (
                        datetime.fromtimestamp(start_ts, tz=timezone.utc)
                        if start_ts
                        else None
                    ),
                    "start_time_local": (
                        datetime.fromtimestamp(start_ts) if start_ts else None
                    ),
                    "distance_meters": item.get("distance"),
                    "_raw": item,
                }
            )
        return activities

    def download_fit(self, activity: dict) -> tuple[bytes, str]:
        meta_url = (
            f"{self._base_url()}/activity/detail/download?labelId={activity['activity_id']}"
            f"&sportType={activity.get('sport_type_raw')}&fileType=4"
        )
        try:
            meta = requests.post(
                meta_url, headers={"accesstoken": self.connect.access_token}, timeout=10
            ).json()
        except Exception:
            self._refresh()
            meta = requests.post(
                meta_url, headers={"accesstoken": self.connect.access_token}, timeout=10
            ).json()
        if meta.get("result") != "0000":
            raise ValueError(f"获取下载链接失败: {meta.get('message')}")
        download_url = meta.get("data", {}).get("fileUrl")
        file_response = requests.get(download_url, stream=True, timeout=30)
        file_response.raise_for_status()
        return file_response.content, f"coros_activity_{activity['activity_id']}.fit"

    def upload_fit(self, fit_data: bytes, filename: str) -> dict:
        """将 FIT 推送到本会话持有的 Coros 账号（保持原有 OSS 中转逻辑不变）。"""
        os.makedirs(GARMIN_FIT_DIR, exist_ok=True)
        file_path = os.path.join(GARMIN_FIT_DIR, f"{filename}.zip")

        # 兼容传入裸 FIT 字节的情况（一键同步传入的是解压后的 FIT），先打包成合法 ZIP
        if not fit_data.startswith(b"PK"):
            buf = io.BytesIO()
            inner_name = f"{os.path.splitext(filename)[0]}.fit"
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(inner_name, fit_data)
            fit_data = buf.getvalue()

        with open(file_path, "wb") as fb:
            fb.write(fit_data)

        filesize = os.path.getsize(file_path)
        md5_hash = calculate_md5_file(file_path)

        # 根据区域选择 Coros 自有对象存储（阿里云 / AWS）
        oss_path = f"fit_zip/{self.connect.guid}/{md5_hash}.zip"
        oss_client = AliOssClient() if str(self.connect.region) == "2" else AwsOssClient()
        oss_client.multipart_upload(file_path, oss_path)

        team_api = self._base_url()
        upload_url = f"{team_api}/activity/fit/import"
        rid = int(self.connect.region) if self.connect.region else 1
        sts = STS_CONFIG.get(rid, STS_CONFIG[1])
        params = {
            "source": 1,
            "timezone": 32,
            "bucket": sts["bucket"],
            "md5": md5_hash,
            "size": filesize,
            "object": oss_path,
            "serviceName": sts["service"],
            "oriFileName": f"{filename}.zip",
        }
        try:
            with log_request(
                current_user=self.current_user,
                req_url=upload_url,
                req_method="POST",
                req_params=params,
                log_type="upload",
                module_name="coros",
                op_desc=f"高驰上传运动文件{filename}",
            ) as ctx:
                res = requests.post(
                    upload_url,
                    headers={
                        "accesstoken": self.connect.access_token,
                        "Accept": "application/json, text/plain, */*",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"jsonParameter": json.dumps(params)},
                    timeout=60,
                ).json()
                ctx["response"] = res
        except Exception as e:
            return {"status": "error", "message": f"上传异常: {str(e)}"}
        if res.get("result") == "0000" and res.get("data", {}).get("status") == 2:
            return {"status": "success", "message": "已成功同步到高驰", "data": res}
        return {
            "status": "error",
            "message": f"高驰导入失败: {res.get('message', '未知错误')}",
            "details": res,
        }
