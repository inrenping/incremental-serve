"""COROS OSS 临时凭证缓存。

一键同步会在线程池里并发上传多条活动，若每条都新建一个 OSS 客户端，
就会重复请求一次 STS（每条 1~2 次网络往返，并发下还容易触发平台风控）。

临时凭证只与「区域 + 桶 + service + Coros access_token」有关，与单次上传无关，
因此可以安全地按维度缓存一段时间复用；token 换了自然落到不同的 key 上。
"""

import hashlib
import threading
import time

# 凭证有效期通常 1 小时，这里提前 30 分钟过期，留出上传耗时余量
DEFAULT_TTL_SECONDS = 1800
# 上限保护：缓存项过多时整体清空，避免长期运行内存缓慢增长
MAX_ENTRIES = 256

_lock = threading.Lock()
_cache: dict[tuple, tuple[str, float]] = {}


def _token_digest(access_token: str | None) -> str:
    return hashlib.md5((access_token or "").encode("utf-8")).hexdigest()


def _key(region, bucket: str, service: str, v, access_token: str | None) -> tuple:
    return (int(region or 0), bucket, service, v, _token_digest(access_token))


def get(region, bucket: str, service: str, v, access_token: str | None) -> str | None:
    """命中且未过期时返回原始凭证串，否则返回 None。"""
    key = _key(region, bucket, service, v, access_token)
    with _lock:
        hit = _cache.get(key)
    if not hit:
        return None
    credentials, expires_at = hit
    if expires_at <= time.monotonic():
        with _lock:
            if _cache.get(key) is hit:
                _cache.pop(key, None)
        return None
    return credentials


def put(
    region,
    bucket: str,
    service: str,
    v,
    access_token: str | None,
    credentials: str,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> None:
    key = _key(region, bucket, service, v, access_token)
    with _lock:
        if len(_cache) >= MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (credentials, time.monotonic() + ttl)


def clear() -> None:
    """清空缓存（测试或强制刷新时使用）。"""
    with _lock:
        _cache.clear()
