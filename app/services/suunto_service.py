import os
import time
import base64
import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.base_connect import BaseConnect
from app.models.base_activity import BaseActivity
from app.models.user import User
from app.utils.crypto_utils import CryptoUtils
from app.utils.logger_utils import log_operation_async, log_request
from app.services import suunto_sml


# ---------------------------------------------------------------------------
# 颂拓（Suunto）私有 API 常量
#
# 以下签名密钥与算法逆向自官方 App 安装包 com.stt.android.suunto（v6.8.13），
# 与开源实现 bulislaw/suuntool（MIT）完全一致。登录 login2 需要带 TOTP 签名，
# 不是裸密码。请知悉：调用的是未公开 API，可能随时无通知变更，且违反 Suunto TOS
# （与本项目现有的 COROS 逆向接入属同一类风险，用户已接受）。
# ---------------------------------------------------------------------------

PACKAGE_NAME = "com.stt.android.suunto"
APP_VERSION_CODE = "6008013"
USER_AGENT = f"{PACKAGE_NAME}/{APP_VERSION_CODE}"

# 登录签名密钥（三段 base64，经 KeyObfuscator XOR 还原）
LOGIN_KEY_PART1 = "FBkubDYmN28bWVQLLTsWFxcmaRB"
LOGIN_KEY_PART2 = "fN2AqIBc/IRAoNgshbxgnOGUVGlU3LC0xL0AuXXXXMXY"
LOGIN_KEY_PART3 = "RWQ4zIi0PWz4hekc1QGNTPlciNhEKV1teYSIkDGYY"

# TOTP 主密钥（PBKDF2 派生用）
TOTP_KEY_PART1 = "FBkubDYmN28bWVQLLTsWWhI+NAtILCNlPQc5Y"
TOTP_KEY_PART2 = "BgiMRYjKA99Jj4HHFIqLmomOFttBQchNzcZU0QrODcDWz4hekc1QGNTPlciNhEKGl5GPDkzFyVX"
TOTP_OBFUSCATION_KEY = "Bh8nsTyCeC0Ql2drMen78awk84AE3ZxW"


# ---------------------------------------------------------------------------
# 密钥还原与签名（移植自 suuntool/internal/auth）
# ---------------------------------------------------------------------------


def _utf8_replace(b: bytes) -> bytes:
    """逐字节无效 UTF-8 替换为 U+FFFD（EF BF BD），与 Go 的 utf8Replace 行为一致。"""
    return b.decode("utf-8", errors="replace").encode("utf-8")


def _key_obfuscator(s: bytes, pkg: bytes) -> bytes:
    """byte-wise XOR of s against repeating pkg bytes（com.stt.android.billing.KeyObfuscator.a）。"""
    out = bytearray(len(s))
    for i, b in enumerate(s):
        out[i] = b ^ pkg[i % len(pkg)]
    return bytes(out)


def _derive_obfuscated_secret(parts: list[str], pkg: str) -> str:
    joined = "".join(parts)
    raw = base64.b64decode(joined)
    mid = _utf8_replace(raw)
    xored = _key_obfuscator(mid, pkg.encode("utf-8"))
    return _utf8_replace(xored).decode("utf-8")


_DERIVE_LOGIN_SECRET = _derive_obfuscated_secret(
    [LOGIN_KEY_PART1, LOGIN_KEY_PART2, LOGIN_KEY_PART3], PACKAGE_NAME
)
_DERIVE_TOTP_MASTER_SECRET = _derive_obfuscated_secret(
    [TOTP_KEY_PART1, TOTP_KEY_PART2], TOTP_OBFUSCATION_KEY
)


def _random_salt() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hotp6(key: bytes, counter: int) -> str:
    buf = counter.to_bytes(8, "big")
    mac = hmac.new(key, buf, hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (
        (mac[off] & 0x7F) << 24
        | (mac[off + 1] << 16)
        | (mac[off + 2] << 8)
        | mac[off + 3]
    )
    return f"{code % 1000000:06d}"


def generate_totp(salt: str, offset_ms: int = 0) -> str:
    """基于邮箱的 6 位动态码（PBKDF2-SHA1 + RFC6238），salt 即登录邮箱。"""
    master = _DERIVE_TOTP_MASTER_SECRET
    pwd = bytes([c & 0xFF for c in master.encode("utf-8")])
    key = hashlib.pbkdf2_hmac("sha1", pwd, salt.encode("utf-8"), 100, 32)
    now = _now_ms() + offset_ms
    counter = now // 30000
    return _hotp6(key, counter)


def sign_params(path: str, params: list[tuple[str, str]]) -> str:
    """SHA-256("POST&" + path + "&k=v..." + "&secret=<login_secret>") 的 base64url(no-pad)。"""
    s = "POST&" + path
    for k, v in params:
        s += "&" + k + "=" + v
    s += "&secret=" + _DERIVE_LOGIN_SECRET
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# HTTP 基础
# ---------------------------------------------------------------------------


def _base_url(region: str) -> str:
    """按 region 解析 base URL。国际版硬编码（可用 SUUNTO_INTL_BASE_URL 覆盖），
    国内版走 SUUNTO_CN_BASE_URL 环境变量——抓到国内版 host 后填进去即生效，零代码改动。"""
    region = (region or "intl").lower()
    if region == "cn":
        url = os.getenv("SUUNTO_CN_BASE_URL", "")
        if not url:
            raise HTTPException(
                status_code=500,
                detail="国内版后端地址未配置，请在环境变量中设置 SUUNTO_CN_BASE_URL",
            )
        return url.rstrip("/") + "/"
    return (
        os.getenv(
            "SUUNTO_INTL_BASE_URL",
            "https://api.sports-tracker.com/apiserver/v1/",
        ).rstrip("/")
        + "/"
    )


def _headers(session_key: str = "") -> dict:
    h = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en",
    }
    if session_key:
        h["STTAuthorization"] = session_key
    return h


def _decode_asko(body: bytes) -> tuple[list, dict]:
    """解析 AskoResponse 信封 {error, payload, metadata}。"""
    data = json.loads(body)
    payload = data.get("payload") or []
    metadata = data.get("metadata") or {}
    return payload, metadata


# ---------------------------------------------------------------------------
# 登录 / 令牌
# ---------------------------------------------------------------------------


def perform_suunto_login(
    id: int,
    account: str,
    encrypted_password: str,
    region: str,
    db: Session,
    current_user: User,
) -> BaseConnect:
    """执行颂拓登录（login2）并保存会话密钥。

    encrypted_password 是前端用 NEXT_PUBLIC_KEY(AES) 加密后的密文，
    这里解密得到原始密码再发往 login2。
    """
    secret_key = os.getenv("SECRET_KEY")
    try:
        raw_password = CryptoUtils.decrypt(encrypted_password, secret_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密码解密失败: {str(e)}")

    base_url = _base_url(region)
    totp = generate_totp(account, 0)
    sig = sign_params("login2", [("l", account), ("p", raw_password), ("totp", totp)])
    salt = _random_salt()
    ts = _now_ms()

    form = {
        "l": account,
        "p": raw_password,
        "totp": totp,
        "timestamp": str(ts),
        "salt": salt,
        "signature": sig,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-login-email-verification-enabled": "true",
        "User-Agent": USER_AGENT,
        "Accept-Language": "en",
    }

    try:
        with log_request(
            current_user=current_user,
            req_url=base_url + "login2",
            req_method="POST",
            req_params={"l": account},
            log_type="login",
            module_name="suunto",
            op_desc="颂拓模拟登录",
        ) as ctx:
            resp = requests.post(base_url + "login2", data=form, headers=headers, timeout=10)
            ctx["response"] = resp
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"颂拓登录失败: {str(e)}")

    session_key = data.get("sessionkey")
    if not session_key:
        raise HTTPException(status_code=400, detail=f"颂拓登录失败: {data}")

    suunto_auth = None
    if id:
        suunto_auth = (
            db.query(BaseConnect)
            .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == id)
            .first()
        )
    if not suunto_auth:
        suunto_auth = BaseConnect(user_id=current_user.id)
        db.add(suunto_auth)

    suunto_auth.source_type = "suunto"
    suunto_auth.account = account
    suunto_auth.encrypted_password = encrypted_password  # 存 AES 密文，relogin 时再解密
    suunto_auth.access_token = session_key
    suunto_auth.guid = data.get("userKey")
    suunto_auth.region = region
    suunto_auth.is_active = True
    suunto_auth.updated_at = datetime.now(timezone.utc)
    db.commit()
    return suunto_auth


def test_suunto_token(connect_id: int, db: Session, current_user: User) -> bool:
    """用一次轻量请求探测会话密钥是否有效。"""
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == connect_id)
        .first()
    )
    if not base_connect or not base_connect.access_token:
        return False
    base_url = _base_url(base_connect.region)
    try:
        resp = requests.get(
            base_url + "workouts?since=0&limit=1&offset=0",
            headers=_headers(base_connect.access_token),
            timeout=10,
        )
        if resp.status_code in (401, 403):
            return False
        resp.raise_for_status()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 活动列表 / FIT 下载
# ---------------------------------------------------------------------------


def list_workouts(
    session_key: str, region: str, since: int = 0, limit: int = 100
) -> tuple[list, dict]:
    """拉一页活动。返回 (items, metadata)，metadata 含 until 游标。"""
    base_url = _base_url(region)
    resp = requests.get(
        base_url + f"workouts?since={since}&limit={limit}&offset=0",
        headers=_headers(session_key),
        timeout=10,
    )
    resp.raise_for_status()
    return _decode_asko(resp.content)


def download_fit_bytes(session_key: str, key: str, region: str) -> bytes:
    """下载单个活动的 FIT 二进制（GET workout/exportFit/{key}）。"""
    base_url = _base_url(region)
    resp = requests.get(
        base_url + f"workout/exportFit/{key}",
        headers=_headers(session_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def get_workout_sml(session_key: str, key: str, region: str) -> dict:
    """拉取某个活动的 JSON SML（GET /v1/workouts/{key}/sml）。

    主要用于：拿真实样本回来比对 fit->SML 转换器造出的字段名是否一致，
    尤其是 Summary 块（目前转换器里的 Summary 结构是未经真实样本逐字校验的）。
    """
    base_url = _base_url(region)
    resp = requests.get(
        base_url + f"workouts/{key}/sml",
        headers=_headers(session_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def upload_sml(session_key: str, sml: dict, region: str, extensions: dict = None) -> dict:
    """上传 SML 到颂拓（POST /v1/workout，multipart）。

    颂拓私有 API 的写活动端点收的是 **SML**（JSON 形态）；这里直接把
    ``fit_bytes_to_sml`` 产出的 dict 序列化后作为 ``filePart`` 发送。
    鉴权用 STTAuthorization（sessionKey），该端点不需要 x-totp（与 suuntool 一致）。

    Args:
        sml: ``{"Data": {"Samples": [...]}, "Summary": {...}}`` 结构。
        extensions: 可选，附加的 extensions JSON（通常传 None）。
    """
    base_url = _base_url(region)
    data = json.dumps(sml).encode("utf-8")
    files = {
        "filePart": ("workout.sml", data, "application/octet-stream"),
    }
    if extensions is not None:
        files["workoutExtensionsPart"] = (
            "extensions.json",
            json.dumps(extensions).encode("utf-8"),
            "application/json",
        )
    resp = requests.post(
        base_url + "workout",
        files=files,
        headers=_headers(session_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize_workout(it: dict) -> dict:
    """把 Suunto workouts 列表项映射成与 BaseActivity 对齐的归一化字典。"""
    start_ts = it.get("startTime")
    start_dt = None
    if start_ts:
        start_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    return {
        "activity_id": str(it.get("key")),
        "source_type": "suunto",
        "activity_name": "",
        "sport_type_raw": str(it.get("activityId")) if it.get("activityId") is not None else None,
        "start_time_gmt": start_dt,
        "start_time_local": datetime.fromtimestamp(start_ts / 1000) if start_ts else None,
        "distance_meters": it.get("totalDistance"),
        "duration_seconds": it.get("totalTime"),
        "calories": it.get("energyConsumption"),
        "average_hr": (it.get("hrdata") or {}).get("avg"),
        "max_hr": (it.get("hrdata") or {}).get("max"),
        "elevation_gain": it.get("totalAscent"),
        "elevation_loss": it.get("totalDescent"),
        "_raw": it,
    }


def pull_full_suunto_activities(
    db: Session, current_user: User, connect_id: int, incremental: bool = True
) -> dict:
    """同步颂拓运动记录（与 pull_full_coros_activities 同构）。"""
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

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
        raise HTTPException(status_code=404, detail="未找到有效的颂拓授权配置，请先绑定账号")

    base_connect = base_connect_service.perform_relogin(
        base_connect.id, db=db, current_user=current_user
    )
    session_key = base_connect.access_token
    region = base_connect.region

    total_count = 0
    total_fetched = 0
    new_saved_count = 0
    since = 0
    stop_fetching = False

    while True:
        try:
            items, meta = list_workouts(session_key, region, since=since, limit=100)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"获取颂拓数据失败: {str(e)}")

        if not items:
            break

        if since == 0:
            # 粗略总数：用本页长度估算；服务端未直接返回 count
            total_count = len(items)

        keys = [str(it.get("key")) for it in items if it.get("key")]
        existing_ids = set()
        if keys:
            existing_ids = {
                lid
                for (lid,) in db.query(BaseActivity.activity_id)
                .filter(BaseActivity.activity_id.in_(keys))
                .all()
            }

        for it in items:
            key = str(it.get("key"))
            if key in existing_ids:
                if incremental:
                    stop_fetching = True
                    break
                continue
            norm = _normalize_workout(it)
            start_gmt = norm["start_time_gmt"]
            start_local = norm["start_time_local"]
            new_activity = BaseActivity(
                base_connect_id=base_connect.id,
                user_id=current_user.id,
                source_type="suunto",
                activity_id=key,
                activity_name=norm["activity_name"],
                sport_type_raw=norm["sport_type_raw"],
                start_time_gmt=start_gmt,
                start_time_local=start_local,
                end_time_gmt=(
                    datetime.fromtimestamp(it.get("stopTime") / 1000, tz=timezone.utc)
                    if it.get("stopTime")
                    else None
                ),
                distance_meters=norm["distance_meters"],
                duration_seconds=norm["duration_seconds"],
                calories=norm["calories"],
                average_hr=norm["average_hr"],
                max_hr=norm["max_hr"],
                elevation_gain=norm["elevation_gain"],
                elevation_loss=norm["elevation_loss"],
            )
            db.add(new_activity)
            new_saved_count += 1

        total_fetched += len(items)
        # metadata.until 是 unix 毫秒游标；服务端以字符串形式返回（大整数防 JS
        # 精度丢失），所以这里必须显式转成 int，否则下一行 str <= int 会抛 TypeError。
        until_raw = meta.get("until")
        try:
            until = int(until_raw) if until_raw is not None else 0
        except (TypeError, ValueError):
            until = 0
        if stop_fetching or until <= since:
            break
        since = until

    base_connect.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "success",
        "total_in_platform": total_count,
        "fetched_count": total_fetched,
        "new_saved_count": new_saved_count,
    }


def download_suunto_activity_response(
    db: Session, current_user: User, connect_id: int, activity_id: int
) -> tuple[requests.Response, str]:
    """获取颂拓 FIT 文件流及建议文件名（与 download_coros_activity_response 同构）。"""
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

    suunto_auth = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.is_active == True,
            BaseConnect.id == connect_id,
        )
        .first()
    )
    if not suunto_auth:
        raise HTTPException(status_code=404, detail="未找到有效的颂拓授权配置")

    activity = (
        db.query(BaseActivity)
        .filter(BaseActivity.user_id == current_user.id, BaseActivity.id == activity_id)
        .first()
    )
    if not activity:
        raise HTTPException(status_code=404, detail="未找到运动记录")

    suunto_auth = base_connect_service.perform_relogin(
        suunto_auth.id, db=db, current_user=current_user
    )
    session_key = suunto_auth.access_token
    region = suunto_auth.region
    url = _base_url(region) + f"workout/exportFit/{activity.activity_id}"

    with log_request(
        current_user=current_user,
        req_url=url,
        req_method="GET",
        req_params=None,
        log_type="download",
        module_name="suunto",
        op_desc=f"下载颂拓运动文件suunto_activity_{activity.activity_id}.fit",
    ) as ctx:
        file_response = requests.get(url, headers=_headers(session_key), timeout=30)
        ctx["response"] = None
    log_operation_async(
        user_id=current_user.id,
        log_type="DOWNLOAD",
        module_name="suunto",
        op_desc=f"下载颂拓运动文件suunto_activity_{activity.activity_id}.fit",
    )
    file_response.raise_for_status()
    return file_response, f"suunto_activity_{activity.activity_id}.fit"


def fetch_latest_suunto_activities(
    config: BaseConnect, count: int, db: Session, current_user: User
) -> list[dict]:
    """直接从颂拓拉取最新 count 条（不写库），供一键同步比较使用。"""
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

    config = base_connect_service.perform_relogin(config.id, db=db, current_user=current_user)
    if not isinstance(config, BaseConnect):
        raise HTTPException(status_code=400, detail="颂拓鉴权失败，请重新绑定账号")

    items, _ = list_workouts(config.access_token, config.region, since=0, limit=count)
    activities = []
    for it in items:
        key = str(it.get("key"))
        start_ts = it.get("startTime")
        activities.append(
            {
                "activity_id": key,
                "source_type": "suunto",
                "activity_name": "",
                "sport_type_raw": str(it.get("activityId")) if it.get("activityId") is not None else None,
                "start_time_gmt": (
                    datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
                    if start_ts
                    else None
                ),
                "start_time_local": datetime.fromtimestamp(start_ts / 1000) if start_ts else None,
                "distance_meters": it.get("totalDistance"),
                "_raw": it,
            }
        )
    return activities


def download_suunto_activity_fit(
    config: BaseConnect, activity: dict, db: Session, current_user: User
) -> tuple[bytes, str]:
    """根据平台原始活动直接下载 FIT，返回 (字节, 文件名)。"""
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

    config = base_connect_service.perform_relogin(config.id, db=db, current_user=current_user)
    if not isinstance(config, BaseConnect):
        raise HTTPException(status_code=400, detail="颂拓鉴权失败，请重新绑定账号")

    data = download_fit_bytes(config.access_token, activity["activity_id"], config.region)
    return data, f"suunto_activity_{activity['activity_id']}.fit"


# ---------------------------------------------------------------------------
# 单连接会话（供 platform_session.build_session 使用）
# ---------------------------------------------------------------------------


class SuuntoSession:
    """单个 Suunto 连接的独立会话。"""

    def __init__(self, connect: BaseConnect, db: Session, current_user: User):
        self.connect = connect
        self.db = db
        self.current_user = current_user
        self._lock = threading.Lock()

    def _snapshot(self) -> tuple:
        with self._lock:
            return (
                self.connect.region,
                self.connect.access_token,
                self.connect.account,
                self.connect.encrypted_password,
            )

    def _refresh(self):
        from app.db.session import SessionLocal
        from app.services import suunto_service

        with self._lock:
            with SessionLocal() as s:
                self.connect = suunto_service.perform_suunto_login(
                    id=self.connect.id,
                    account=self.connect.account,
                    encrypted_password=self.connect.encrypted_password,
                    region=self.connect.region,
                    db=s,
                    current_user=self.current_user,
                )

    def list_activities(self, count: int) -> list[dict]:
        region, access_token, *_ = self._snapshot()
        try:
            items, _ = list_workouts(access_token, region, since=0, limit=count)
        except Exception:
            self._refresh()
            region, access_token, *_ = self._snapshot()
            items, _ = list_workouts(access_token, region, since=0, limit=count)
        return [_normalize_workout(it) for it in items]

    def download_fit(self, activity: dict) -> tuple[bytes, str]:
        region, access_token, *_ = self._snapshot()
        try:
            data = download_fit_bytes(access_token, activity["activity_id"], region)
        except Exception:
            self._refresh()
            region, access_token, *_ = self._snapshot()
            data = download_fit_bytes(access_token, activity["activity_id"], region)
        return data, f"suunto_activity_{activity['activity_id']}.fit"

    def upload_fit(self, file_data: bytes, filename: str, activity_id: int = None) -> dict:
        """把一段 FIT 上传到颂拓（FIT -> JSON SML -> POST /v1/workout）。

        颂拓私有 API 不支持直接收 FIT，需要先把 FIT 转成它的 SML 格式再上传。
        """
        region, access_token, account, _ = self._snapshot()
        try:
            device_source = f"suunto-{abs(hash(account)) % 10 ** 9}"
            sml = suunto_sml.fit_bytes_to_sml(
                file_data,
                device_source=device_source,
                activity_id=activity_id,
            )
            result = upload_sml(access_token, sml, region)
        except Exception as e:
            return {"status": "error", "message": f"上传到颂拓失败: {str(e)}"}
        return {
            "status": "success",
            "message": "已上传到颂拓",
            "detail": result,
        }
