"""颂拓上传探测用的**轻量 HTTP 入口**（不碰数据库）。

为什么单独存在
--------------
``app.services.suunto_service`` 在 import 时会连带拉起 SQLAlchemy 模型、
数据库连接（需要 ``psycopg``）、FastAPI 依赖链。探测脚本只想发几个 HTTP 请求，
却因为这些依赖装不全而**根本跑不起来**。

这里把上传探测真正需要的四样东西抽出来，零数据库依赖：

- ``base_url(region)``   —— 同 ``suunto_service._base_url``
- ``headers(session_key)`` —— 同 ``suunto_service._headers``
- ``USER_AGENT``         —— 与 suuntool 的 ``internal/auth/keys.go`` 逐字一致
- ``check_session()``    —— 上传前的只读自检

**一致性要求**：``suunto_service._base_url`` / ``_headers`` 若有改动，
必须同步改这里（两处都用 ``assert`` 在测试里校验过）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

# 与 suuntool internal/auth/keys.go 的 UserAgent 逐字一致
PACKAGE_NAME = "com.stt.android.suunto"
APP_VERSION_CODE = "6008013"
USER_AGENT = f"{PACKAGE_NAME}/{APP_VERSION_CODE}"

# 与 suuntool internal/api/client.go 的DefaultBaseURL 一致
DEFAULT_INTL_BASE_URL = "https://api.sports-tracker.com/apiserver/v1/"

# ---------------------------------------------------------------------------
# 密钥还原与签名（移植自 suuntool/internal/auth，与 suunto_service 逐字一致）
# 纯计算，不依赖数据库
# ---------------------------------------------------------------------------

LOGIN_KEY_PART1 = "FBkubDYmN28bWVQLLTsWFxcmaRB"
LOGIN_KEY_PART2 = "fN2AqIBc/IRAoNgshbxgnOGUVGlU3LC0xL0AuXXXXMXY"
LOGIN_KEY_PART3 = "RWQ4zIi0PWz4hekc1QGNTPlciNhEKV1teYSIkDGYY"
TOTP_KEY_PART1 = "FBkubDYmN28bWVQLLTsWWhI+NAtILCNlPQc5Y"
TOTP_KEY_PART2 = "BgiMRYjKA99Jj4HHFIqLmomOFttBQchNzcZU0QrODcDWz4hekc1QGNTPlciNhEKGl5GPDkzFyVX"
TOTP_OBFUSCATION_KEY = "Bh8nsTyCeC0Ql2drMen78awk84AE3ZxW"


def _utf8_replace(b: bytes) -> bytes:
    """逐字节无效 UTF-8 替换为 U+FFFD（EF BF BD），与 Go 的 utf8Replace 行为一致。"""
    return b.decode("utf-8", errors="replace").encode("utf-8")


def _key_obfuscator(s: bytes, pkg: bytes) -> bytes:
    """byte-wise XOR of s against repeating pkg bytes。"""
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
    counter = (_now_ms() + offset_ms) // 30000
    return _hotp6(key, counter)


def sign_params(path: str, params: list[tuple[str, str]]) -> str:
    """SHA-256("POST&" + path + "&k=v..." + "&secret=...") 的 base64url(no-pad)。"""
    s = "POST&" + path
    for k, v in params:
        s += "&" + k + "=" + v
    s += "&secret=" + _DERIVE_LOGIN_SECRET
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def login(account: str, password: str, region: str = "intl") -> str:
    """执行 ``login2`` 登录，返回 sessionKey。**纯 HTTP，不碰数据库。**

    与 ``suunto_service.perform_suunto_login`` 的签名/表单构造逐字一致，
    区别只在于不落库。

    Raises:
        RuntimeError: 登录失败（响应里没有 sessionkey）。
    """
    import requests

    url = base_url(region)
    totp = generate_totp(account, 0)
    sig = sign_params("login2", [("l", account), ("p", password), ("totp", totp)])
    form = {
        "l": account,
        "p": password,
        "totp": totp,
        "timestamp": str(_now_ms()),
        "salt": base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii"),
        "signature": sig,
    }
    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-login-email-verification-enabled": "true",
        "User-Agent": USER_AGENT,
        "Accept-Language": "en",
    }
    resp = requests.post(url + "login2", data=form, headers=hdrs, timeout=15)
    data = resp.json()
    session_key = data.get("sessionkey")
    if not session_key:
        raise RuntimeError(f"登录失败: HTTP {resp.status_code} {data}")
    return session_key


def base_url(region: str = "intl") -> str:
    """按 region 解析 base URL。逻辑与 suunto_service._base_url 保持一致。"""
    region = (region or "intl").lower()
    if region == "cn":
        url = os.getenv("SUUNTO_CN_BASE_URL", "")
        if not url:
            raise RuntimeError(
                "国内版后端地址未配置，请设置环境变量 SUUNTO_CN_BASE_URL"
            )
        return url.rstrip("/") + "/"
    return (
        os.getenv("SUUNTO_INTL_BASE_URL", DEFAULT_INTL_BASE_URL).rstrip("/") + "/"
    )


def headers(session_key: str = "") -> dict:
    """构造请求头。与 suuntool client.go 的 newRequest 对齐：
    固定 ``User-Agent`` + ``Accept-Language: en`` + ``STTAuthorization``。
    """
    h = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en",
    }
    if session_key:
        h["STTAuthorization"] = session_key
    return h


def check_session(session_key: str, region: str = "intl") -> tuple[bool, dict]:
    """上传前的**只读**会话自检。

    为什么必须先做这一步：实测原始 FIT 直传与 SML XML 直传报**完全相同的
    523**，而 FIT 是二进制、不存在「XML 结构不对」这回事。两种本质不同的载荷
    报同一句话，说明服务端在进入格式判定**之前**就没拿到有效载荷 ——
    问题更可能在 sessionKey / 权限。先用只读端点验证凭据，一刀切开两类问题：

    - 只读端点不通 → sessionKey 失效或无权限，修格式没有意义
    - 只读端点通过 → 凭据没问题，523 可确定是格式问题

    只读，不写入任何数据。

    Returns:
        ``(是否通过, {端点名: 状态码或异常信息})``
    """
    import requests

    url = base_url(region)
    hdrs = headers(session_key)
    results: dict[str, object] = {}
    ok = False

    # 挑几个最轻的只读端点，逐个确认鉴权是否通
    probes = [
        ("user", "当前用户"),
        ("workouts/count", "训练数量"),
        ("activitytypes", "运动类型"),
    ]
    for path, label in probes:
        try:
            resp = requests.get(url + path, headers=hdrs, timeout=30)
            results[label] = resp.status_code
            if resp.status_code == 200:
                ok = True
        except Exception as e:  # noqa: BLE001
            results[label] = f"异常: {type(e).__name__}: {e}"
    return ok, results
