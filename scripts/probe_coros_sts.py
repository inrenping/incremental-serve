#!/usr/bin/env python3
"""探测 COROS OSS STS 端点的可用性。

用途：上传到高驰（COROS）前需要先向 faq.coros.com 换 OSS 临时密钥（STS）。
这个接口是逆向出来的私有接口，随时可能被上游改动。跑这个脚本可以快速判断：

1. 网络与端点是否可达（DNS + TCP 443 + HTTPS 握手）
2. v1 通道（/openapi/oss/sts）是否还能签发凭证
3. v2 通道（/openapi/v2/oss/sts）是否存在、需要哪种认证头（需要真实 accessToken）

用法：
    # 只做免登录探测（不需要任何凭据）
    python scripts/probe_coros_sts.py

    # 带真实 COROS accessToken 验证 v2 通道（token 只用于请求，不会被完整打印）
    python scripts/probe_coros_sts.py --token <CPL-coros-token 或 access_token>

    # 只测某个区域
    python scripts/probe_coros_sts.py --region cn

退出码：0 = 至少一个区域能拿到凭证；1 = 全部失败；2 = 网络不可达。
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FAQ_HOST = "faq.coros.com"
V1_PATH = "/openapi/oss/sts"
V2_PATH = "/openapi/v2/oss/sts"
APP_ID = "1660188068672619112"

# bucket / service / sign —— sign 与 bucket 一一绑定，混用会返回 401 signature error
REGIONS = {
    "cn": ("coros-oss", "aliyun", "9AD4AA35AAFEE6BB1E847A76848D58DF"),
    "en": ("coros-s3", "aws", "E34EF0E34A498A54A9C3EAEFC12B7CAF"),
    "eu": ("eu-coros", "aws", "877571111A1EE5316E4B590103D4B5B3"),
}

TIMEOUT = 15
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def mask(token: str) -> str:
    """只显示首尾几位，避免 token 落到日志里。"""
    if not token:
        return "<空>"
    if len(token) <= 12:
        return token[:4] + "…"
    return f"{token[:6]}…{token[-4:]} (len={len(token)})"


def check_network(host: str = FAQ_HOST, port: int = 443) -> bool:
    print(f"[网络] 解析并连接 {host}:{port} …")
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as e:
        print(f"[网络] DNS 解析失败: {e}")
        return False
    try:
        start = time.time()
        with socket.create_connection((host, port), timeout=TIMEOUT):
            print(f"[网络] 可达，IP={ip}，TCP 耗时 {int((time.time() - start) * 1000)} ms")
            return True
    except OSError as e:
        print(f"[网络] TCP 连接失败（IP={ip}）: {e}")
        return False


def http_get(url: str, headers: dict | None = None, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), int((time.time() - start) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), int((time.time() - start) * 1000)
    except Exception as e:  # noqa: BLE001
        return -1, str(e), int((time.time() - start) * 1000)


def parse_code(body: str):
    """COROS 的 HTTP 状态码恒为 200，真正的业务码在 body 的 code 字段里。"""
    try:
        return json.loads(body).get("code")
    except ValueError:
        return None


def summarize(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body[:160].replace("\n", " ")
    code = data.get("code")
    msg = data.get("msg") or data.get("message")
    extra = ""
    if isinstance(data.get("data"), dict):
        d = data["data"]
        if d.get("credentials"):
            extra = " | 已下发 credentials ✓"
        elif d.get("errorCode"):
            extra = f" | errorCode={d['errorCode']}"
    return f"code={code} msg={msg}{extra}"


def build_v1_url(region: str) -> str:
    bucket, service, sign = REGIONS[region]
    qs = urllib.parse.urlencode(
        {"bucket": bucket, "service": service, "app_id": APP_ID, "sign": sign, "v": 2}
    )
    return f"https://{FAQ_HOST}{V1_PATH}?{qs}"


def build_v2_url(region: str) -> str:
    bucket, service, sign = REGIONS[region]
    qs = urllib.parse.urlencode(
        {"bucket": bucket, "service": service, "app_id": APP_ID, "sign": sign, "v": 2}
    )
    return f"https://{FAQ_HOST}{V2_PATH}?{qs}"


def probe_v1(regions: list[str]) -> dict[str, bool]:
    print("\n=== v1 通道 /openapi/oss/sts（免登录，硬编码 sign） ===")
    ok = {}
    for region in regions:
        status, body, ms = http_get(build_v1_url(region))
        usable = parse_code(body) == 200
        ok[region] = usable
        print(f"  [{region:2}] HTTP {status} ({ms} ms) {'✓ 可用' if usable else '✗ 不可用'}")
        print(f"        {summarize(body)}")
    return ok


def probe_v2(regions: list[str], token: str | None) -> dict[str, bool]:
    print("\n=== v2 通道 /openapi/v2/oss/sts ===")
    if not token:
        print("  未提供 --token，只做无凭据探测（预期 401，用来确认端点存在）")
        styles = [("无认证头", None)]
    else:
        print(f"  token = {mask(token)}，依次尝试不同认证方式")
        styles = [
            ("header accesstoken", {"accesstoken": token}),
            ("header Authorization: Bearer", {"Authorization": f"Bearer {token}"}),
            ("header Authorization 裸值", {"Authorization": token}),
            ("header x-access-token", {"x-access-token": token}),
        ]

    ok: dict[str, bool] = {}
    for region in regions:
        region_ok = False
        for name, headers in styles:
            url = build_v2_url(region)
            if name.startswith("query"):
                url += "&" + urllib.parse.urlencode({"accesstoken": token or ""})
            status, body, ms = http_get(url, headers)
            good = parse_code(body) == 200
            region_ok = region_ok or good
            print(f"  [{region:2}] {name:32} HTTP {status} ({ms} ms) {'✓' if good else ''}")
            print(f"        {summarize(body)}")
        if token:
            url = build_v2_url(region) + "&" + urllib.parse.urlencode({"accesstoken": token})
            status, body, ms = http_get(url)
            good = parse_code(body) == 200
            region_ok = region_ok or good
            print(f"  [{region:2}] {'query 参数 accesstoken':32} HTTP {status} ({ms} ms) {'✓' if good else ''}")
            print(f"        {summarize(body)}")
        ok[region] = region_ok
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 COROS OSS STS 端点可用性")
    parser.add_argument("--token", default=None, help="真实 COROS accessToken，用于验证 v2 通道")
    parser.add_argument("--region", default=None, choices=list(REGIONS), help="只测指定区域")
    args = parser.parse_args()

    regions = [args.region] if args.region else list(REGIONS)

    if not check_network():
        print("\n结论：网络不可达，先检查出口网络/代理（访问国内服务时记得清掉 HTTP_PROXY）。")
        return 2

    v1_ok = probe_v1(regions)
    v2_ok = probe_v2(regions, args.token)

    print("\n=== 结论 ===")
    for region in regions:
        if v1_ok.get(region):
            print(f"  [{region:2}] v1 可用，上传链路应正常")
        elif v2_ok.get(region):
            print(f"  [{region:2}] v1 已停用，但 v2 能拿到凭证 —— 按输出里的认证方式改造 OSS 客户端即可")
        elif args.token:
            print(f"  [{region:2}] v1 停用，v2 用当前 token 也拿不到凭证（可能是 token 过期或认证方式不对）")
        else:
            print(f"  [{region:2}] v1 停用；加 --token 再跑一次才能判断 v2 是否可用")

    return 0 if any(v1_ok.values()) or any(v2_ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
