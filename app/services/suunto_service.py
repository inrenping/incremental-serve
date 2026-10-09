import os
import time
import base64
import hashlib
import hmac
import json
import logging
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
# 运动类型表
#
# Sports Tracker 的 workouts 列表 **不返回用户自定义标题**（没有 name 字段），
# 只有数字 activityId。英文枚举名逆向自官方 APK（与 suuntool internal/api/
# endpoints/activity_types.go 一致，89/98 是枚举空洞）；中文名为本项目映射，
# 用于填 activity_name —— 否则同步回来的活动名是空字符串。
# ---------------------------------------------------------------------------

SUUNTO_ACTIVITY_NAMES = {
    0: "WALKING", 1: "RUNNING", 2: "CYCLING", 3: "CROSS_COUNTRY_SKIING",
    4: "OTHER_1", 5: "OTHER_2", 6: "OTHER_3", 7: "OTHER_4", 8: "OTHER_5",
    9: "OTHER_6", 10: "MOUNTAIN_BIKING", 11: "HIKING", 12: "ROLLER_SKATING",
    13: "DOWNHILL_SKIING", 14: "PADDLING", 15: "ROWING", 16: "GOLF",
    17: "INDOOR", 18: "PARKOUR", 19: "BALLGAMES", 20: "OUTDOOR_GYM",
    21: "SWIMMING", 22: "TRAIL_RUNNING", 23: "GYM", 24: "NORDIC_WALKING",
    25: "HORSEBACK_RIDING", 26: "MOTOR_SPORTS", 27: "SKATEBOARDING",
    28: "WATER_SPORTS", 29: "CLIMBING", 30: "SNOWBOARDING", 31: "SKI_TOURING",
    32: "FITNESS_CLASS", 33: "SOCCER", 34: "TENNIS", 35: "BASKETBALL",
    36: "BADMINTON", 37: "BASEBALL", 38: "VOLLEYBALL", 39: "AMERICAN_FOOTBALL",
    40: "TABLE_TENNIS", 41: "RACQUETBALL", 42: "SQUASH", 43: "FLOORBALL",
    44: "HANDBALL", 45: "SOFTBALL", 46: "BOWLING", 47: "CRICKET", 48: "RUGBY",
    49: "ICE_SKATING", 50: "ICE_HOCKEY", 51: "YOGA", 52: "INDOOR_CYCLING",
    53: "TREADMILL", 54: "CROSSFIT", 55: "CROSSTRAINER", 56: "ROLLER_SKIING",
    57: "INDOOR_ROWING", 58: "STRETCHING", 59: "TRACK_AND_FIELD",
    60: "ORIENTEERING", 61: "SUP", 62: "COMBAT_SPORTS", 63: "KETTLEBELL",
    64: "DANCING", 65: "SNOWSHOEING", 66: "FRISBEE_GOLF", 67: "FUTSAL",
    68: "MULTISPORT", 69: "AEROBICS", 70: "TREKKING", 71: "SAILING",
    72: "KAYAKING", 73: "CIRCUIT_TRAINING", 74: "TRIATHLON", 75: "PADEL",
    76: "CHEERLEADING", 77: "BOXING", 78: "SCUBADIVING", 79: "FREEDIVING",
    80: "ADVENTURE_RACING", 81: "GYMNASTICS", 82: "CANOEING",
    83: "MOUNTAINEERING", 84: "TELEMARKSKIING", 85: "OPENWATER_SWIMMING",
    86: "WINDSURFING", 87: "KITESURFING_KITING", 88: "PARAGLIDING",
    90: "SNORKELING", 91: "SURFING", 92: "SWIMRUN", 93: "DUATHLON",
    94: "AQUATHLON", 95: "OBSTACLE_RACING", 96: "FISHING", 97: "HUNTING",
    99: "GRAVEL_CYCLING", 100: "MERMAIDING", 101: "SPEARFISHING",
    102: "JUMP_ROPE", 103: "TRACK_RUNNING", 104: "CALISTHENICS",
    105: "E_BIKING", 106: "E_MTB", 107: "BACKCOUNTRY_SKIING",
    108: "WHEELCHAIR", 109: "HAND_CYCLING", 110: "SPLIT_BOARDING",
    111: "BIATHLON", 112: "MEDITATION", 113: "FIELD_HOCKEY", 114: "CYCLOCROSS",
    115: "VERTICAL_RUN", 116: "SKI_MOUNTAINEERING", 117: "SKATE_SKIING",
    118: "CLASSIC_SKIING", 119: "CHORES", 120: "PILATES", 121: "NEW_YOGA",
}

SUUNTO_ACTIVITY_NAMES_ZH = {
    0: "步行", 1: "跑步", 2: "骑行", 3: "越野滑雪", 4: "其他", 5: "其他",
    6: "其他", 7: "其他", 8: "其他", 9: "其他", 10: "山地骑行", 11: "徒步",
    12: "轮滑", 13: "高山滑雪", 14: "划桨", 15: "赛艇", 16: "高尔夫",
    17: "室内运动", 18: "跑酷", 19: "球类运动", 20: "户外健身", 21: "游泳",
    22: "越野跑", 23: "健身", 24: "北欧健走", 25: "骑马", 26: "赛车",
    27: "滑板", 28: "水上运动", 29: "攀岩", 30: "单板滑雪", 31: "滑雪穿越",
    32: "团体健身", 33: "足球", 34: "网球", 35: "篮球", 36: "羽毛球",
    37: "棒球", 38: "排球", 39: "美式橄榄球", 40: "乒乓球", 41: "壁球",
    42: "壁式网球", 43: "地板球", 44: "手球", 45: "垒球", 46: "保龄球",
    47: "板球", 48: "橄榄球", 49: "滑冰", 50: "冰球", 51: "瑜伽",
    52: "室内骑行", 53: "跑步机", 54: "CrossFit", 55: "交叉训练",
    56: "轮滑滑雪", 57: "室内划船", 58: "拉伸", 59: "田径", 60: "定向越野",
    61: "桨板(SUP)", 62: "格斗", 63: "壶铃", 64: "舞蹈", 65: "雪地徒步",
    66: "飞盘高尔夫", 67: "五人制足球", 68: "多项运动", 69: "有氧操",
    70: "徒步旅行", 71: "帆船", 72: "皮划艇", 73: "循环训练", 74: "铁人三项",
    75: "板式网球", 76: "啦啦操", 77: "拳击", 78: "水肺潜水", 79: "自由潜水",
    80: "探险赛", 81: "体操", 82: "独木舟", 83: "高山攀登", 84: "远程滑雪",
    85: "公开水域游泳", 86: "风帆冲浪", 87: "风筝冲浪", 88: "滑翔伞",
    90: "浮潜", 91: "冲浪", 92: "游泳跑步", 93: "两项赛", 94: "水中两项",
    95: "障碍赛", 96: "钓鱼", 97: "狩猎", 99: "砾石骑行", 100: "美人鱼泳",
    101: "鱼枪捕鱼", 102: "跳绳", 103: "场地跑", 104: "自重健身",
    105: "电助力骑行", 106: "电助力山地车", 107: "野滑雪", 108: "轮椅",
    109: "手推自行车", 110: "分体滑板", 111: "冬季两项", 112: "冥想",
    113: "曲棍球", 114: "越野自行车", 115: "垂直跑", 116: "滑雪登山",
    117: "滑轮滑雪", 118: "传统滑雪", 119: "家务", 120: "普拉提",
    121: "新瑜伽",
}


def _suunto_activity_name(activity_id) -> str:
    """把数字 activityId 映射成中文运动名（列表接口没有用户自定义标题）。"""
    if activity_id is None:
        return ""
    try:
        idx = int(activity_id)
    except (TypeError, ValueError):
        return ""
    name = SUUNTO_ACTIVITY_NAMES_ZH.get(idx)
    if name:
        return name
    # 枚举空洞（89/98）或新版 APK 新增的类型：退回英文枚举名，都没有则标注原始 id
    return SUUNTO_ACTIVITY_NAMES.get(idx) or f"颂拓运动 {idx}"


# sport_type_raw 用规范 slug（对齐 app/utils/activity_type_config.py 的 ACTIVITY_CONFIG
# 与前端 activity-icons 的匹配规则），否则三处都会失效：
#   1) 活动页「运动类型」筛选：后端把 key(100) 展开成 name(running) 再匹配 sport_type_raw；
#   2) 前端图标：getActivityIconName 查不到就退化成通用 IconActivity；
#   3) 前端类型标签：ActivityTypes.<slug> 查不到就直接显示原始值。
# 没有对应等价类型的 sport_type_raw 存英文枚举名的小写形式（前端仍可按关键字匹配图标）。
SUUNTO_ACTIVITY_TYPE_SLUG = {
    0: "walking", 1: "running", 2: "cycling", 3: "cross_country_skiing",
    10: "mountain_biking", 11: "hiking", 14: "paddlesports", 15: "rowing",
    21: "swimming", 22: "trail_running", 24: "walking", 28: "water_sports",
    30: "resort_skiing_snowboarding_ws", 31: "resort_skiing_snowboarding_ws",
    51: "yoga", 52: "indoor_cycling", 53: "treadmill_running", 59: "track_and_field",
    61: "paddlesports", 70: "hiking", 71: "sailing", 72: "rowing_v2",
    74: "triathlon", 85: "open_water_swimming", 86: "windsurfing",
    91: "surfing", 99: "cycling", 103: "track_running", 105: "e_biking",
    106: "e_mtb", 107: "backcountry_skiing_snowboarding_ws", 114: "cycling",
}


def _suunto_sport_type_raw(activity_id) -> Optional[str]:
    """数字 activityId → 规范运动类型 slug（小写英文）。"""
    if activity_id is None:
        return None
    try:
        idx = int(activity_id)
    except (TypeError, ValueError):
        return None
    slug = SUUNTO_ACTIVITY_TYPE_SLUG.get(idx)
    if slug:
        return slug
    return (SUUNTO_ACTIVITY_NAMES.get(idx) or "").lower() or None


def upload_fit_to_suunto(
    config: BaseConnect,
    file_data: bytes,
    db: Session = None,
    current_user: User = None,
) -> dict:
    """把一段 FIT 上传到指定颂拓账号（走官方 FIT 导入通道）。

    供"一键推送/单条推送"这类不经过 SuuntoSession 的路径直接调用。

    Args:
        config: 目标 BaseConnect（source_type="suunto"），会先 relogin 刷新 sessionKey。
        file_data: 源平台下载下来的 FIT 字节。
    """
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

    if db is not None and current_user is not None:
        config = base_connect_service.perform_relogin(
            config.id, db=db, current_user=current_user
        )
    if not config or not config.access_token:
        return {"status": "error", "message": "颂拓授权失效，请重新绑定账号"}

    result = upload_workout(
        config.access_token,
        config.region,
        file_data,
    )
    return {
        "status": "success",
        "message": "已上传到颂拓",
        "detail": result,
    }


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


# 2026-10-09 实测：``https://{cloud-api,api}.suunto.cn/apiserver/v1/servertime``
# 均返回 200，说明国内版是**独立服务集群**，但沿用同一套 ``/apiserver/v1/`` 路径
# 结构（fit.suunto.cn 这个官方FIT 导入门户用的正是 cloud-api.suunto.cn）。
# 默认值使国内账号开箱即用，无需先配环境变量；仍可用 SUUNTO_CN_BASE_URL 覆盖。
SUUNTO_CN_BASE_URL_DEFAULT = "https://cloud-api.suunto.cn/apiserver/v1/"

# 服务**根** URL（不含 ``/apiserver/v1/``）。官方 FIT 导入门户（fit.suunto.cn）
# 的 axios baseURL 就是 ``https://api.suunto.cn``，其 FIT 导入路径是
# ``/apiserver/management/user/import/fit``——注意是 ``/apiserver/`` 开头、
# **没有 ``/v1``**。
#
# 踩坑记录（2026-10-09）：若拿 _base_url()（含 /apiserver/v1/）去拼该路径，会得到
# ``/apiserver/v1/management/user/import/fit``，实测返回 **404**；去掉 /v1 才是
# 正确的 ``/apiserver/management/...``，实测 403（仅缺认证）。两者仅差一个 ``/v1``，
# 但结果一个 404 一个 403，极易误判为"端点不存在"或"鉴权失败"。
SUUNTO_CN_ROOT_DEFAULT = "https://cloud-api.suunto.cn"
SUUNTO_INTL_ROOT_DEFAULT = "https://api.sports-tracker.com"


def _service_root(region: str) -> str:
    """返回服务**根** URL（不含 ``/apiserver/v1/``），供 ``/apiserver/*`` 非 v1 路径使用。

    从 :func:`_base_url` 的值反推：去掉结尾的 ``apiserver/v1/``。这样环境变量
    （``SUUNTO_INTL_BASE_URL`` / ``SUUNTO_CN_BASE_URL``）仍能统一配置，不需要新增变量。
    """
    base = _base_url(region)
    for suffix in ("apiserver/v1/", "apiserver/v1"):
        if base.endswith(suffix):
            return base[: -len(suffix)].rstrip("/")
    # 兜底：若环境变量给了不含 apiserver 的根，直接去掉尾部斜杠
    return base.rstrip("/")


def _base_url(region: str) -> str:
    """按 region 解析 base URL。

    国际版默认 ``api.sports-tracker.com``；国内版默认 ``cloud-api.suunto.cn``。
    两边都可用对应环境变量覆盖（``SUUNTO_INTL_BASE_URL`` / ``SUUNTO_CN_BASE_URL``）。

    重要：打错服务集群是 523 的一个独立成因——国际版集群不认识国内账号的
    sessionKey/载荷时会返回与"载荷没识别"同一句文案，容易误判成格式问题。
    """
    region = (region or "intl").lower()
    if region == "cn":
        url = os.getenv("SUUNTO_CN_BASE_URL") or SUUNTO_CN_BASE_URL_DEFAULT
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


def upload_fit_import(
    session_key: str,
    region: str,
    fit_bytes: bytes,
    filename: str = "activity.fit",
    field: str = "file",
) -> dict:
    """用**官方 FIT 导入门户**同款接口上传原始 FIT。

    2026-10-09 逆向 ``fit.suunto.cn``（颂拓官方运动记录导入门户）的前端 bundle
    得到确切协议——这是官方**实际在用**的FIT 导入通道：

    - 基址 ``https://api.suunto.cn``
    - ``POST /apiserver/management/user/import/fit``（单个 FIT）
      / ``import/fits``（批量 ZIP）
    - axios 全局拦截器强制注入``STTAuthorization: <token>``
    - 字段名 ``file``（源码 ``a.append("file", z.raw)``，组件 ``ImportActive``，
      类型固定 ``fit``）

    注意：门户用的是 ``api.suunto.cn``（国际版也是这个 host）。国内版对应
    ``cloud-api.suunto.cn``，由 ``_base_url(region)`` 决定。

    Args:
        session_key: STTAuthorization 的值。
        region: ``intl`` / ``cn``。
        fit_bytes: 源平台下载的原始 FIT 字节。
        filename: multipart 的 filename。
        field: multipart 字段名，默认 ``file``（官方门户实测值）。

    Returns:
        服务端响应的结构化结果（含 ``status_code`` / ``response``）。

    Raises:
        HTTPException: HTTP >= 400，或响应体里带 error。
    """
    headers = _headers(session_key)
    # 注意用_service_root()（不含 /apiserver/v1/），不能用 _base_url()。
    # 官方门户 baseURL 是服务根，其 FIT 导入路径为 /apiserver/management/...（无 /v1）；
    # 误用 _base_url() 会多出一层 /v1，实测 404。
    url = _service_root(region) + "/apiserver/management/user/import/fit"
    resp = requests.post(
        url,
        files={field: (filename, fit_bytes, "application/octet-stream")},
        headers=headers,
        timeout=180,
    )

    body_text = (resp.text or "").strip()
    parsed = None
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001
        parsed = None

    result = {"status_code": resp.status_code, "body": body_text[:2000]}
    if isinstance(parsed, dict):
        result["response"] = parsed
        # Asko 信封：{"error": ..., "payload": ..., "metadata": ...}
        err = parsed.get("error")
        if err:
            result["error"] = err

    if resp.status_code >= 400:
        detail = result.get("error") or body_text[:500] or f"HTTP {resp.status_code}"
        raise HTTPException(
            status_code=502,
            detail=f"颂拓FIT 导入失败(HTTP {resp.status_code}): {detail}",
        )
    if isinstance(parsed, dict) and parsed.get("error"):
        raise HTTPException(
            status_code=502,
            detail=f"颂拓 FIT 导入被拒绝: {parsed.get('error')}",
        )
    return result


def upload_workout(
    session_key: str,
    region: str,
    fit_bytes: bytes,
) -> dict:
    """把 FIT 上传到颂拓（官方 FIT 导入通道）。

    走官方 FIT 导入门户同款端点
    ``POST /apiserver/management/user/import/fit``、字段名 ``file``、直传原始 FIT，
    见 :func:`upload_fit_import`。这是逆向 ``fit.suunto.cn`` 前端 bundle 得到的
    官方真实协议，也是官方用户实际导入历史 FIT 走的通道。

    历史背景（已废弃，勿再走）：此前打的是 suuntool 逆向出的
    ``/apiserver/v1/workout``（SML 端点），2026-10-09 实测该端点下 11 种形态
    （raw/multipart × filePart/file/sml/binary/SML/Sml × 多种 Content-Type）
    **全部**返回 ``523 neither binary or SML was provided``，且四种字段名结果完全
    一致 —— 根因是**打错了端点**，不是字段名或载荷内容。相关SML 代码已移除。

    Args:
        session_key: 目标账号的 sessionKey。
        region: ``intl`` / ``cn``。
        fit_bytes: 源平台下载下来的 FIT 字节。

    Returns:
        ``{"status": "success", "used_format": "official-import-fit", ...}``。

    Raises:
        HTTPException: 502，detail 含服务端真实响应体（便于按返回码定位）。
    """
    logger = logging.getLogger(__name__)

    # 2026-10-09 逆向 fit.suunto.cn 确认真实协议为
    # POST /apiserver/management/user/import/fit + 字段名 ``file``。
    # 设 SUUNTO_SKIP_FIT_IMPORT=1 可跳过（仅用于诊断对照）。
    if os.getenv("SUUNTO_SKIP_FIT_IMPORT", "0") == "1":
        raise HTTPException(
            status_code=502,
            detail="颂拓上传失败：SUUNTO_SKIP_FIT_IMPORT=1 已跳过官方 FIT 导入端点",
        )

    try:
        result = upload_fit_import(session_key, region, fit_bytes)
    except HTTPException as e:
        # 注意：项目未配置 logging，uvicorn 的 dictConfig 默认
        # disable_existing_loggers=True 会静默禁用本模块的 logger.warning，
        # 导致服务端真实响应体根本不进 journald。改走 print（stdout，绕过
        # logging），保证失败详情一定可见。
        print(f"[suunto] 官方 FIT 导入端点失败详情: {e.detail}", flush=True)
        logger.warning("官方 FIT 导入端点失败: %s", e.detail)
        raise HTTPException(
            status_code=502,
            detail=f"颂拓上传失败(official-import-fit={_brief_error(e.detail)}): {e.detail}",
        )

    print(
        f"[suunto] 官方 FIT 导入端点上传成功: HTTP {result.get('status_code')}",
        flush=True,
    )
    logger.info("颂拓 FIT 导入成功（官方 import/fit 端点）")
    return {
        "status": "success",
        "used_format": "official-import-fit",
        "detail": result,
        "payload": (result.get("response") or {}).get("payload"),
    }


def _brief_error(detail: str) -> str:
    """把一长串错误压成 ``HTTP500/523`` 这种摘要，便于在汇总里并排列出。"""
    import re

    text = str(detail)
    m = re.search(r"HTTP (\d+)", text)
    http = m.group(1) if m else "?"
    m = re.search(r"['\"]code['\"]:\s*['\"]?(\d+)", text)
    code = m.group(1) if m else ""
    return f"HTTP{http}/{code}" if code else f"HTTP{http}"


def _position_to_latlon(pos: Optional[dict]) -> tuple:
    """把 Sports Tracker 的位置块转成 (纬度, 经度) 度数。

    真实响应（@petitchevalroux/sports-tracker-client 样本）里位置是
    ``{"x": <经度>, "y": <纬度>}`` —— x 是经度、y 是纬度；
    suuntool 的 LatLon 结构体写成 latitude/longitude 是错的（它从不消费该字段）。
    这里两种键名都兼容。

    另外该接口历史上曾以弧度返回过经纬度，而 workouts 列表接口是**度数**；为防
    哪天服务端口径变化，这里做一次范围判定：超出度数范围且落在弧度范围内就换算。
    """
    if not isinstance(pos, dict):
        return None, None
    lat = pos.get("y")
    lon = pos.get("x")
    if lat is None and lon is None:
        lat = pos.get("latitude")
        lon = pos.get("longitude")
    if lat is None or lon is None:
        return None, None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None, None
    # 弧度兜底判定（正常数据不会走到这里）
    if abs(lat) <= 3.15 and abs(lon) <= 3.15:
        import math

        lat = math.degrees(lat)
        lon = math.degrees(lon)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None, None
    return lat, lon


def _num(*candidates):
    """取第一个非 None 的数值候选（用于多来源字段的优先级回退）。"""
    for c in candidates:
        if c is None:
            continue
        try:
            f = float(c)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _int_or_none(value):
    """Integer 列不要浮点：四舍五入成 int，无值返回 None。"""
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _normalize_workout(it: dict) -> dict:
    """把 Suunto workouts 列表项映射成与 BaseActivity 对齐的归一化字典。

    覆盖列表接口能给的**全部**字段：起止时间、距离/时长、爬升下降、热量、
    平均/最大心率、平均/最大速度、平均/最大踏频、起点经纬度、活动名。
    """
    start_ts = it.get("startTime")
    stop_ts = it.get("stopTime")
    start_dt = None
    if start_ts:
        start_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    end_dt = None
    if stop_ts:
        end_dt = datetime.fromtimestamp(stop_ts / 1000, tz=timezone.utc)

    hr = it.get("hrdata") or {}
    cadence = it.get("cadence") or {}
    distance = it.get("totalDistance")
    duration = it.get("totalTime")
    start_lat, start_lon = _position_to_latlon(it.get("startPosition"))

    return {
        "activity_id": str(it.get("key") or it.get("workoutKey") or ""),
        "source_type": "suunto",
        # 列表接口没有用户标题，用运动类型名兜底
        "activity_name": _suunto_activity_name(it.get("activityId")),
        # 规范 slug（供筛选/图标/标签匹配），原始枚举 id 存进 sport_mode_raw
        "sport_type_raw": _suunto_sport_type_raw(it.get("activityId")),
        "sport_mode_raw": _int_or_none(it.get("activityId")),
        "start_time_gmt": start_dt,
        "start_time_local": datetime.fromtimestamp(start_ts / 1000) if start_ts else None,
        "end_time_gmt": end_dt,
        "distance_meters": distance,
        "duration_seconds": duration,
        # 列表接口没有净运动时长，总时长先兜住，避免前端显示空
        "moving_duration_seconds": duration,
        "calories": it.get("energyConsumption"),
        # workoutAvgHR/workoutMaxHR 是本次活动的值；avg/max 是设备档位，优先用前者
        "average_hr": _int_or_none(_num(hr.get("workoutAvgHR"), hr.get("avg"))),
        "max_hr": _int_or_none(_num(hr.get("workoutMaxHR"), hr.get("max"))),
        "average_cadence": _int_or_none(_num(cadence.get("avg"))),
        "max_cadence": _int_or_none(_num(cadence.get("max"))),
        # avgSpeed 单位是 m/s；缺失时用 距离/时长 兜底
        "average_speed": _num(
            it.get("avgSpeed"),
            (distance / duration) if distance and duration else None,
        ),
        "max_speed": _num(it.get("maxSpeed")),
        "start_lat": start_lat,
        "start_lon": start_lon,
        "elevation_gain": it.get("totalAscent"),
        "elevation_loss": it.get("totalDescent"),
        "_raw": it,
    }


def _backfill_missing_fields(row: BaseActivity, norm: dict) -> bool:
    """只把库里为空的字段补上，不覆盖已有值。返回是否发生了补齐。

    例外：旧版本把数字 activityId 直接写进了 ``sport_type_raw``，那种值要**替换**
    成规范 slug —— 否则前端的类型筛选/图标/标签对这几条永远失效。
    """
    changed = False
    for field in (
        "activity_name",
        "sport_type_raw",
        "sport_mode_raw",
        "end_time_gmt",
        "moving_duration_seconds",
        "average_cadence",
        "max_cadence",
        "average_speed",
        "max_speed",
        "start_lat",
        "start_lon",
    ):
        value = norm.get(field)
        if value in (None, ""):
            continue
        current = getattr(row, field, None)
        if field == "sport_type_raw" and isinstance(current, str) and current.isdigit():
            setattr(row, field, value)
            changed = True
            continue
        if current in (None, ""):
            setattr(row, field, value)
            changed = True
    return changed


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
    total_backfilled = 0
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

        keys = [
            str(it.get("key") or it.get("workoutKey"))
            for it in items
            if it.get("key") or it.get("workoutKey")
        ]
        existing_rows = {}
        if keys:
            existing_rows = {
                str(row.activity_id): row
                for row in db.query(BaseActivity)
                .filter(
                    BaseActivity.user_id == current_user.id,
                    BaseActivity.source_type == "suunto",
                    BaseActivity.activity_id.in_(keys),
                )
                .all()
            }

        for it in items:
            key = str(it.get("key") or it.get("workoutKey") or "")
            if not key:
                continue
            norm = _normalize_workout(it)
            existing = existing_rows.get(key)
            if existing is not None:
                # 旧数据里这些字段可能是空的（早期版本只填了距离/时长/心率），补齐
                if _backfill_missing_fields(existing, norm):
                    total_backfilled += 1
                # 如果增量拉取则停止
                if incremental:
                    stop_fetching = True
                    break
                continue
            # _raw 是原始响应不落库；source_type 已在上面显式给出，避免重复传参
            norm.pop("_raw", None)
            norm.pop("source_type", None)
            new_activity = BaseActivity(
                base_connect_id=base_connect.id,
                user_id=current_user.id,
                source_type="suunto",
                **norm,
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
        "backfilled_count": total_backfilled,
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
        norm = _normalize_workout(it)
        if not norm["activity_id"]:
            continue
        activities.append(norm)
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
        """把一段 FIT 上传到颂拓（官方 FIT 导入通道）。

        具体走 ``POST /apiserver/management/user/import/fit``，逻辑见
        :func:`upload_workout`。``filename`` / ``activity_id`` 为统一调用契约保留，
        官方导入通道不消费这两个参数。
        """
        region, access_token, _account, _ = self._snapshot()
        try:
            result = upload_workout(access_token, region, file_data)
        except Exception as e:
            return {"status": "error", "message": f"上传到颂拓失败: {str(e)}"}
        return {
            "status": "success",
            "message": "已上传到颂拓",
            "detail": result,
        }
