"""高驰上传链路的回归测试。

覆盖 2026-10 COROS STS v1→v2 升级时踩到的坑：
一键同步的 OSS 客户端是无参构造，拿不到 access_token，
于是跳过 v2 代理只打已下线的 v1 端点，导致批量同步全线失败。

这些用例全部离线运行：网络与数据库都被打桩。
"""

import base64
import json as _json
import os
import urllib3
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from app.services import coros_upload
from app.services.oss import sts_cache
from app.services.oss.ali_oss_client import AliOssClient
from app.services.oss.aws_oss_client import AwsOssClient
from app.services.oss.sts_token_error import StsTokenError
from app.services.platform_session import CorosSession
from app.utils.config import GARMIN_FIT_DIR

ALI_CRED = base64.b64encode(
    _json.dumps(
        {
            "SecurityToken": "st",
            "AccessKeyId": "ak",
            "AccessKeySecret": "sk",
            "Region": "oss-cn-beijing",
            "Bucket": "coros-oss",
        }
    ).encode()
).decode()

AWS_CRED = base64.b64encode(
    _json.dumps(
        {
            "SessionToken": "st",
            "AccessKeyId": "ak",
            "SecretAccessKey": "sk",
            "Region": "eu-central-1",
            "Bucket": "coros-s3",
        }
    ).encode()
).decode()


class _FakeHTTPResp:
    def __init__(self, payload):
        self.data = _json.dumps(payload).encode()


class _FakeJsonResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@contextmanager
def _noop_log_request(*args, **kwargs):
    yield {}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """清空凭证缓存、屏蔽日志落库，并拦截 STS 网络请求。"""
    sts_cache.clear()
    monkeypatch.setattr(coros_upload, "log_request", _noop_log_request)

    hits = {"n": 0, "urls": []}

    def fake_request(self, method, url, **kwargs):
        hits["n"] += 1
        hits["urls"].append(url)
        if "faq.coros.com" in url:
            # 旧端点已下线，返回非 JSON
            return _FakeHTTPResp({})
        cred = ALI_CRED if "aliyun" in url else AWS_CRED
        return _FakeHTTPResp({"code": 200, "data": {"credentials": cred, "v": 2}})

    monkeypatch.setattr(urllib3.PoolManager, "request", fake_request)
    return hits


@pytest.fixture
def _no_upload(monkeypatch):
    """拦截真正的文件上传，只记录调用。"""
    calls = []

    def fake_put(self, data, key):
        calls.append((type(self).__name__, key, len(data)))
        return key

    monkeypatch.setattr(AliOssClient, "put_object", fake_put)
    monkeypatch.setattr(AliOssClient, "multipart_upload", fake_put)
    monkeypatch.setattr(AwsOssClient, "put_object", fake_put)
    monkeypatch.setattr(AwsOssClient, "multipart_upload", fake_put)
    return calls


def _import_ok():
    return _FakeJsonResp({"result": "0000", "data": {"status": 2, "uploadId": "u1"}})


def test_oss_client_receives_access_token(_isolated):
    """客户端必须拿到 access_token，否则 v2 代理候选会被跳过。"""
    client = coros_upload.build_oss_client(2, "token-A")
    assert client.access_token == "token-A"
    urls = [u for u, _ in client._request_candidates()]
    assert any("/api/proxy/oss/sts" in u for u in urls), urls


def test_legacy_endpoint_is_only_fallback(_isolated):
    """v2 代理排在 v1 之前；v1 只是兜底。"""
    client = coros_upload.build_oss_client(2, "token-A")
    urls = [u for u, _ in client._request_candidates()]
    assert "/api/proxy/oss/sts" in urls[0]
    assert "faq.coros.com" in urls[1]


def test_sts_credentials_are_reused(_isolated):
    """批量同步时同账号只应取一次凭证，避免每条活动都打 STS。"""
    for _ in range(5):
        coros_upload.build_oss_client(2, "token-A")
    assert _isolated["n"] == 1, _isolated["urls"]

    coros_upload.build_oss_client(2, "token-B")
    assert _isolated["n"] == 2, "换 token 后应重新取凭证"


def test_bucket_follows_region_config(_isolated):
    """区域 1 必须落到 coros-s3，而不是客户端默认的 eu-coros。"""
    cn = coros_upload.build_oss_client(2, "token-A")
    assert isinstance(cn, AliOssClient) and cn.bucket == "coros-oss"

    en = coros_upload.build_oss_client(1, "token-A")
    assert isinstance(en, AwsOssClient) and en.bucket == "coros-s3"


def test_upload_fit_end_to_end(_isolated, _no_upload):
    """小体积 FIT 走一次性 PUT，上传内容就是打包后的 ZIP 本身。"""
    with patch.object(coros_upload.requests, "post", return_value=_import_ok()):
        result = coros_upload.upload_fit(
            b"raw-fit-bytes", "998877", guid="guid-1", region=2, access_token="token-A"
        )
    assert result["status"] == "success", result
    assert _no_upload and _no_upload[0][0] == "AliOssClient"
    assert _no_upload[0][1].startswith("fit_zip/guid-1/")

    expected = coros_upload.pack_zip(b"raw-fit-bytes", "998877")
    assert _no_upload[0][2] == len(expected)


def test_upload_fit_writes_no_temp_file(_isolated, _no_upload):
    """上传全程在内存完成，不再往 GARMIN_FIT_DIR 落盘（旧实现会持续堆积 zip）。"""
    with patch.object(coros_upload.requests, "post", return_value=_import_ok()):
        coros_upload.upload_fit(
            b"raw", "no-disk-1", guid="guid-1", region=2, access_token="token-A"
        )
    assert not os.path.exists(os.path.join(GARMIN_FIT_DIR, "no-disk-1.zip"))


def test_non_cn_region_uses_aws(_isolated, _no_upload):
    with patch.object(coros_upload.requests, "post", return_value=_import_ok()):
        coros_upload.upload_fit(
            b"raw", "112233", guid="g", region=1, access_token="token-A"
        )
    assert _no_upload and _no_upload[0][0] == "AwsOssClient"


def test_coros_session_retries_after_refresh(_isolated, monkeypatch):
    """凭证失败（多为 token 过期）时，一键同步应刷新令牌后重试一次。"""
    connect = Mock()
    connect.id = 1
    connect.guid = "g"
    connect.region = 2
    connect.access_token = "old-token"

    session = CorosSession(connect, Mock(), Mock())

    def fake_refresh(self):
        self.connect.access_token = "new-token"

    monkeypatch.setattr(CorosSession, "_refresh", fake_refresh)

    def fail_then_ok(fit_data, filename, *, guid, region, access_token, current_user=None):
        if access_token == "old-token":
            raise StsTokenError("凭证失效")
        return {"status": "success", "message": "重试成功"}

    with patch.object(coros_upload, "upload_fit", fail_then_ok):
        result = session.upload_fit(b"raw", "556677")

    assert result["status"] == "success"
    assert connect.access_token == "new-token"
