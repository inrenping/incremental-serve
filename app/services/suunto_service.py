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


# slug → activityId 反查表（上传时把源活动的运动类型带过去）
SUUNTO_ACTIVITY_ID_BY_SLUG = {}
for _aid, _slug in SUUNTO_ACTIVITY_TYPE_SLUG.items():
    SUUNTO_ACTIVITY_ID_BY_SLUG.setdefault(_slug, _aid)


def suunto_activity_id_from_slug(slug: Optional[str]) -> Optional[int]:
    """规范 slug → 颂拓 activityId（上 FIT→SML 时标注运动类型用）。"""
    if not slug:
        return None
    return SUUNTO_ACTIVITY_ID_BY_SLUG.get(str(slug).strip().lower())


def upload_fit_to_suunto(
    config: BaseConnect,
    file_data: bytes,
    db: Session = None,
    current_user: User = None,
    sport_type_raw: str = None,
) -> dict:
    """把一段 FIT 上传到指定颂拓账号（SML XML → 原始 FIT 自动降级）。

    供"一键推送/单条推送"这类不经过 SuuntoSession 的路径直接调用。

    服务端 ``POST /v1/workout`` 会自己嗅探载荷类型（认 ``binary`` 与 ``SML`` 两种），
    具体由 :func:`upload_workout` 处理降级。

    Args:
        config: 目标 BaseConnect（source_type="suunto"），会先 relogin 刷新 sessionKey。
        file_data: 源平台下载下来的 FIT 字节。
        sport_type_raw: 源活动的规范运动类型 slug，用来给SML 标注运动类型。
    """
    from app.services import base_connect_service  # 延迟导入，避免循环依赖

    if db is not None and current_user is not None:
        config = base_connect_service.perform_relogin(
            config.id, db=db, current_user=current_user
        )
    if not config or not config.access_token:
        return {"status": "error", "message": "颂拓授权失效，请重新绑定账号"}

    device_source = f"suunto-{abs(hash(config.account or '')) % 10 ** 9}"
    result = upload_workout(
        config.access_token,
        config.region,
        file_data,
        sport_type_raw=sport_type_raw,
        device_source=device_source,
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


def upload_sml(
    session_key: str,
    sml,
    region: str,
    extensions: dict = None,
    filename: str = "workout.sml",
    field: str = "filePart",
    content_type: str = "application/octet-stream",
    raw: bool = False,
) -> dict:
    """上传 SML 到颂拓（POST /v1/workout）。

    鉴权用 STTAuthorization（sessionKey），该端点不需要 x-totp（与 suuntool 一致）。
    默认形态与 suuntool ``api.WorkoutMultipart`` 一致：multipart、字段名
    ``filePart``、part Content-Type ``application/octet-stream``。

    Args:
        sml: SML 内容。**dict 会自动序列化为 JSON**；传 ``str``/``bytes`` 则按原样
            发送（用于发 legacy SML **XML** —— 实测服务端只认 XML，发 JSON 会 500）。
        extensions: 可选，附加的 extensions JSON（通常传 None）。
        field: multipart 的字段名（``raw=True`` 时忽略）。
        filename: multipart 的 filename（``raw=True`` 时忽略）。
        content_type: ``raw=True`` 时是**整个请求**的 Content-Type；
            否则是 multipart 里那一个 part 的 Content-Type。
        raw: **True = 不用 multipart，直接把载荷作为请求体发送**。
            suuntool 的 guides 上传（``POST /v1/suuntoplus/guides/files``）就是这种
            形态，说明这套后端并非所有上传入口都用 multipart。

    注意：**不使用 raise_for_status**。Sportstracker 服务端把错误放在响应体里
    （Asko 信封的 error 字段），抛异常会把最有价值的排查信息丢掉——之前就是
    只看到一个「500 Internal Server Error」而查不出原因。
    """
    base_url = _base_url(region)
    if isinstance(sml, dict):
        data = json.dumps(sml).encode("utf-8")
    elif isinstance(sml, str):
        data = sml.encode("utf-8")
    else:
        data = sml

    headers = _headers(session_key)

    if raw:
        headers["Content-Type"] = content_type
        resp = requests.post(
            base_url + "workout",
            data=data,
            headers=headers,
            timeout=60,
        )
    else:
        files = {
            field: (filename, data, content_type),
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
            headers=headers,
            timeout=60,
        )

    # 尽量把服务端错误解析成结构化信息
    body_text = (resp.text or "").strip()
    parsed = None
    try:
        parsed = resp.json()
    except Exception:
        parsed = None

    result = {
        "status_code": resp.status_code,
        "body": body_text[:2000],
    }
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
            detail=f"颂拓上传失败(HTTP {resp.status_code}): {detail}",
        )
    if isinstance(parsed, dict) and parsed.get("error"):
        raise HTTPException(
            status_code=502,
            detail=f"颂拓上传被拒绝: {parsed.get('error')}",
        )
    return result


# ---------------------------------------------------------------------------
# 上传「形态矩阵」——为什么需要试多种形态
#
# 2026-10-09 实测：SML XML 与原始 FIT **报完全相同的 523**
# （``Workout was not saved because neither binary or SML was provided``）。
# 已排除凭据：本地用假 sessionKey 打同一端点是 **403 Forbidden**，而生产拿到的是
# 523，说明 sessionKey 有效、请求已经进了业务处理。
#
# 剩下的解释只有一个：**服务端没在它期望的位置找到载荷**，即请求的**形态**不对。
# 旁证：suuntool 的 guides 上传（``POST /v1/suuntoplus/guides/files``）用的是
# **raw body 而不是 multipart**；而 workouts 上传它用 multipart/``filePart``，
# 作者并未声称验证过自己生成的 SML（原话 "This command does NOT generate SML"）。
# 所以 multipart/``filePart`` 这一形态本身也可能是猜的。
#
# 因此这里把候选形态全试一遍，第一个成功即返回，并记进日志。
# **确认哪种形态可用后请精简本表**（每种失败形态都是一次真实网络请求）。
#
# 字段含义：(label, 载荷类型, 是否 raw body, multipart 字段名, filename, Content-Type)
# ---------------------------------------------------------------------------

UPLOAD_VARIANTS: list[tuple[str, str, bool, str, str, str]] = [
    ("raw-octet-sml", "sml", True, None, None, "application/octet-stream"),
    ("raw-xml-sml", "sml", True, None, None, "text/xml"),
    ("part-filePart-octet", "sml", False, "filePart", "workout.sml",
     "application/octet-stream"),
    ("part-filePart-xml", "sml", False, "filePart", "workout.sml",
     "application/xml"),
    ("part-filePart-textxml", "sml", False, "filePart", "workout.sml", "text/xml"),
    ("part-file-sml", "sml", False, "file", "workout.sml",
     "application/octet-stream"),
    ("part-sml-sml", "sml", False, "sml", "workout.sml",
     "application/octet-stream"),
    ("raw-octet-fit", "fit", True, None, None, "application/octet-stream"),
    ("part-binary-fit", "fit", False, "binary", "activity.fit",
     "application/octet-stream"),
    ("part-filePart-fit", "fit", False, "filePart", "activity.fit",
     "application/octet-stream"),
]


def upload_workout(
    session_key: str,
    region: str,
    fit_bytes: bytes,
    activity_id: int = None,
    sport_type_raw: str = None,
    device_source: str = None,
    variants: list = None,
) -> dict:
    """把 FIT 上传到颂拓，**按 :data:`UPLOAD_VARIANTS` 依次尝试各种请求形态**。

    每次尝试都会把「形态 + 服务端返回」写进日志，全部失败才抛 502。
    这样一次线上点击就能拿到完整诊断表，不必去猜。

    设环境变量 ``SUUNTO_UPLOAD_VARIANTS=0`` 可退回「只试 suuntool 同款形态」。

    Args:
        session_key: 目标账号的 sessionKey。
        region: ``intl`` / ``cn``。
        fit_bytes: 源平台下载下来的 FIT 字节。
        activity_id: 显式指定 ActivityType；与 ``sport_type_raw`` 二选一。
        sport_type_raw: 源活动的规范运动类型 slug。
        device_source: SML 里的设备标识，默认按 sessionKey 生成。
        variants: 覆盖默认形态表（测试用）。

    Returns:
        ``{"status": "success", "used_format": <label>, ...}``
    """
    logger = logging.getLogger(__name__)
    device_source = device_source or f"suunto-{abs(hash(session_key)) % 10 ** 9}"
    if activity_id is None:
        activity_id = suunto_activity_id_from_slug(sport_type_raw)

    if variants is None:
        if os.getenv("SUUNTO_UPLOAD_VARIANTS", "1") == "0":
            variants = [
                v for v in UPLOAD_VARIANTS if v[0] == "part-filePart-octet"
            ] + [v for v in UPLOAD_VARIANTS if v[0] == "part-filePart-fit"]
        else:
            variants = UPLOAD_VARIANTS

    payloads: dict[str, bytes] = {"fit": fit_bytes}
    try:
        payloads["sml"] = suunto_sml.fit_bytes_to_sml_xml(
            fit_bytes,
            device_source=device_source,
            activity_id=activity_id,
        )
    except Exception as e:  # noqa: BLE001
        # FIT 里没有可用 record 时 XML 生成会失败，只留原始 FIT
        logger.warning("生成 SML XML 失败，只能发原始 FIT: %s", e)

    errors: list[str] = []
    for label, kind, raw, field, filename, ctype in variants:
        data = payloads.get(kind)
        if data is None:
            continue
        try:
            result = upload_sml(
                session_key,
                data,
                region,
                filename=filename or "workout.sml",
                field=field or "filePart",
                content_type=ctype,
                raw=raw,
            )
            logger.info("颂拓上传成功，形态 = %s", label)
            return {
                "status": "success",
                "used_format": label,
                "detail": result,
                "payload": (result.get("response") or {}).get("payload"),
            }
        except HTTPException as e:
            errors.append(f"{label}: {e.detail}")
            logger.warning("上传形态 %s 失败: %s", label, e.detail)

    raise HTTPException(
        status_code=502,
        detail="颂拓上传失败（已试 %d 种形态）: " % len(errors) + " | ".join(errors),
    )


def _position_to_latlon(pos: Optional[dict]) -> tuple:
    """把 Sports Tracker 的位置块转成 (纬度, 经度) 度数。

    真实响应（@petitchevalroux/sports-tracker-client 样本）里位置是
    ``{"x": <经度>, "y": <纬度>}`` —— x 是经度、y 是纬度；
    suuntool 的 LatLon 结构体写成 latitude/longitude 是错的（它从不消费该字段）。
    这里两种键名都兼容。

    另外 JSON SML 里的经纬度是**弧度**，而 workouts 列表接口是**度数**；为防
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
        """把一段 FIT 上传到颂拓（SML XML → 原始 FIT 自动降级）。

        颂拓私有 API 不支持直接收 FIT 也不收 SML 的 JSON 形态；服务端 ``POST
        /v1/workout`` 会自己嗅探载荷类型（认 ``binary`` 与 ``SML``），具体降级
        逻辑见 :func:`upload_workout`。
        """
        region, access_token, account, _ = self._snapshot()
        try:
            result = upload_workout(
                access_token,
                region,
                file_data,
                activity_id=activity_id,
                device_source=f"suunto-{abs(hash(account)) % 10 ** 9}",
            )
        except Exception as e:
            return {"status": "error", "message": f"上传到颂拓失败: {str(e)}"}
        return {
            "status": "success",
            "message": "已上传到颂拓",
            "detail": result,
        }
