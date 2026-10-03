"""Training Hub 各区域站点域名。

2026-10 起 COROS 把 OSS 临时密钥的签发从 `faq.coros.com/openapi/oss/sts`（v1）
切到了 Training Hub 站点自己的同域代理 `/api/proxy/oss/sts`（v2 通道）：
带 `CPL-coros-token` cookie 请求，只需 `bucket`、`service`、`v` 三个参数，
不再需要硬编码的 `app_id` / `sign`。
"""

WEB_BASE_BY_REGION = {
    1: "https://training.coros.com",
    2: "https://trainingcn.coros.com",
    3: "https://trainingeu.coros.com",
}

DEFAULT_WEB_BASE = WEB_BASE_BY_REGION[1]

STS_PROXY_PATH = "/api/proxy/oss/sts"
COOKIE_NAME = "CPL-coros-token"


def get_web_base(region_id) -> str:
    try:
        return WEB_BASE_BY_REGION.get(int(region_id), DEFAULT_WEB_BASE)
    except (ValueError, TypeError):
        return DEFAULT_WEB_BASE


def build_proxy_url(region_id, bucket: str, service: str, v: int = 2) -> str:
    return f"{get_web_base(region_id)}{STS_PROXY_PATH}?bucket={bucket}&service={service}&v={v}"
