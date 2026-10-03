#!/usr/bin/env python3
"""探测 COROS OSS STS 端点的可用性。

用途：上传到高驰（COROS）前需要先向 COROS 换 OSS 临时密钥（STS）。
这套接口是逆向出来的私有接口，上游随时会改。跑这个脚本可以快速判断当前哪条通道可用：

1. 网络与站点是否可达（DNS + TCP 443）
2. v2 通道：Training Hub 同域代理 `/api/proxy/oss/sts`（2026-10 起启用，需 CPL-coros-token）
3. v1 通道：`faq.coros.com/openapi/oss/sts`（老通道，2026-10 起对全部 bucket 返回 403）

用法：
    # 免登录：只能看到 v1 通道的状态
    python scripts/probe_coros_sts.py

    # 带 COROS token 验证 v2 通道（token 只用于请求，输出里会打码）
    python scripts/probe_coros_sts.py --token <CPL-coros-token>

    # 只测某个区域：cn 中国 / en 国际 / eu 欧洲
    python scripts/probe_coros_sts.py --region cn --token <token>

退出码：0 = 至少一个区域能拿到凭证；1 = 全部失败；2 = 网络不可达。
"""

import argparse
import base64
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_ID = "1660188068672619112"
STS_SALT = "9y78gpoERW4lBNYL"
COOKIE_NAME = "CPL-coros-token"

# region -> (bucket, service, sign, Training Hub 站点域名)
REGIONS = {
    "cn": ("coros-oss", "aliyun", "9AD4AA35AAFEE6BB1E847A76848D58DF", "trainingcn.coros.com"),
    "en": ("coros-s3", "aws", "E34EF0E34A498A54A9C3EAEFC12B7CAF", "training.coros.com"),
    "eu": ("eu-coros", "aws", "877571111A1EE5316E4B590103D4B5B3", "trainingeu.coros.com"),
}
LEGACY_HOST = "faq.coros.com"

TIMEOUT = 20
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def mask(value: str) -> str:
    if not value:
        return "<空>"
    if len(value) <= 12:
        return value[:4] + "…"
    return f"{value[:6]}…{value[-4:]} (len={len(value)})"


def check_network(host: str, port: int = 443) -> bool:
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


def http_get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), int((time.time() - start) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), int((time.time() - start) * 1000)
    except Exception as e:  # noqa: BLE001
        return -1, str(e), int((time.time() - start) * 1000)


def parse_code(body: str):
    """COROS 的 HTTP 状态码恒为 200，真正的业务码在响应体的 code 字段里。"""
    try:
        return json.loads(body).get("code")
    except ValueError:
        return None


def describe_credentials(body: str) -> str:
    """成功时把凭证字段名列出来（值不打印）。"""
    try:
        data = json.loads(body).get("data") or {}
    except ValueError:
        return ""
    raw = data.get("credentials")
    if not raw:
        return ""
    try:
        decoded = json.loads(base64.b64decode(raw.replace(STS_SALT, "") + "==").decode())
    except Exception:  # noqa: BLE001
        return " | credentials 解码失败"
    return " | 凭证字段: " + ", ".join(decoded.keys())


def build_v2_url(region: str) -> str:
    bucket, service, _sign, host = REGIONS[region]
    qs = urllib.parse.urlencode({"bucket": bucket, "service": service, "v": 2})
    return f"https://{host}/api/proxy/oss/sts?{qs}"


def build_v1_url(region: str) -> str:
    bucket, service, sign, _host = REGIONS[region]
    qs = urllib.parse.urlencode(
        {"bucket": bucket, "service": service, "app_id": APP_ID, "sign": sign, "v": 2}
    )
    return f"https://{LEGACY_HOST}/openapi/oss/sts?{qs}"


def probe_v2(regions: list[str], token: str | None) -> dict[str, bool]:
    print("\n=== v2 通道 training*.coros.com/api/proxy/oss/sts ===")
    if not token:
        print("  未提供 --token，跳过（这条通道必须带 CPL-coros-token cookie）")
        return {r: False for r in regions}

    print(f"  token = {mask(token)}")
    ok = {}
    for region in regions:
        url = build_v2_url(region)
        status, body, ms = http_get(url, {"Cookie": f"{COOKIE_NAME}={token}", "Accept": "application/json"})
        good = parse_code(body) == 200
        ok[region] = good
        print(f"  [{region:2}] HTTP {status} ({ms} ms) {'✓ 拿到凭证' if good else '✗ 失败'}")
        print(f"        code={parse_code(body)}{describe_credentials(body)}")
    return ok


def probe_v1(regions: list[str]) -> dict[str, bool]:
    print("\n=== v1 通道 faq.coros.com/openapi/oss/sts（硬编码 sign，2026-10 起停用） ===")
    ok = {}
    for region in regions:
        status, body, ms = http_get(build_v1_url(region))
        usable = parse_code(body) == 200
        ok[region] = usable
        try:
            msg = json.loads(body).get("msg")
        except ValueError:
            msg = body[:80]
        print(f"  [{region:2}] HTTP {status} ({ms} ms) {'✓ 可用' if usable else '✗ 不可用'}")
        print(f"        code={parse_code(body)} msg={msg}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 COROS OSS STS 端点可用性")
    parser.add_argument("--token", default=None, help="COROS accessToken / CPL-coros-token，用于验证 v2 通道")
    parser.add_argument("--region", default=None, choices=list(REGIONS), help="只测指定区域")
    args = parser.parse_args()

    regions = [args.region] if args.region else list(REGIONS)
    if not check_network(REGIONS[regions[0]][3]):
        print("\n结论：网络不可达，先检查出口网络/代理（访问国内服务时记得清掉 HTTP_PROXY）。")
        return 2

    v2_ok = probe_v2(regions, args.token)
    v1_ok = probe_v1(regions)

    print("\n=== 结论 ===")
    for region in regions:
        if v2_ok.get(region):
            print(f"  [{region:2}] v2 通道可用 —— 上传链路应正常")
        elif not args.token:
            print(f"  [{region:2}] v1 已停用；加 --token 再跑一次才能判断 v2 是否可用")
        else:
            print(f"  [{region:2}] v2 也拿不到凭证 —— token 可能失效或不属于该区域")

    return 0 if any(v2_ok.values()) or any(v1_ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
