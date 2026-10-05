"""把 FIT 文件推送到高驰的统一流水线。

背景：单条同步（coros_service）与一键同步（platform_session.CorosSession）原本各自
实现了一遍「打包 ZIP → 算 MD5 → 传 Coros 自有 OSS → 调 activity/fit/import」，
2026-10 COROS 把 STS 从 v1 切到 v2 时只改了前者，一键同步因此全线失败。
这里收敛成一份实现，以后协议再变只需改一处。

约定：
- 不依赖数据库会话、不抛 HTTPException（HTTP 语义是接口层的职责），
  因此可以安全地在线程池 worker 内调用；
- 只有「取 STS 凭证」环节会抛 StsTokenError，调用方可据此刷新 token 后重试。
"""

import hashlib
import io
import json
import os
import zipfile

import requests

from app.services.oss.ali_oss_client import AliOssClient
from app.services.oss.aws_oss_client import AwsOssClient
from app.utils.coros_region_config import REGIONCONFIG
from app.utils.coros_sts_config import STS_CONFIG
from app.utils.logger_utils import log_request

DEFAULT_REGION = 1
# 小于该体积走一次性 PUT（1 次往返），否则走分片（3 次往返）
SIMPLE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024


def _region_id(region) -> int:
    try:
        return int(region)
    except (ValueError, TypeError):
        return DEFAULT_REGION


def team_api_base(region) -> str:
    """根据区域 ID 获取高驰 Team API 的基准 URL。"""
    return REGIONCONFIG.get(_region_id(region), REGIONCONFIG[DEFAULT_REGION])["teamapi"]


def region_sts(region) -> dict:
    """按区域取桶名与 service，缺失时回退到国际区配置。"""
    return STS_CONFIG.get(_region_id(region), STS_CONFIG[DEFAULT_REGION])


def build_oss_client(region, access_token: str | None):
    """按区域构造 Coros 自有对象存储客户端（区域 2 走阿里云，其余走 AWS）。

    注意：必须带上 access_token，否则客户端会跳过 v2 代理只打已下线的 v1 端点。
    """
    rid = _region_id(region)
    sts = region_sts(rid)
    common = {
        "access_token": access_token,
        "region": rid,
        "bucket": sts["bucket"],
        "service": sts["service"],
    }
    return AliOssClient(**common) if rid == 2 else AwsOssClient(**common)


def pack_zip(fit_data: bytes, filename: str) -> bytes:
    """入参可能是 Garmin 返回的原始 ZIP，也可能是解压后的裸 FIT，统一成合法 ZIP。"""
    if fit_data.startswith(b"PK"):
        return fit_data
    buf = io.BytesIO()
    inner_name = f"{os.path.splitext(filename)[0]}.fit"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, fit_data)
    return buf.getvalue()


def upload_fit(
    fit_data: bytes,
    filename: str,
    *,
    guid: str,
    region,
    access_token: str | None,
    current_user=None,
) -> dict:
    """把一条 FIT 上传到高驰账号，返回 {status, message, data}。

    全程在内存完成（不写本地临时文件），不触碰数据库会话，可在线程池内并发调用。
    """
    zip_bytes = pack_zip(fit_data, filename)

    filesize = len(zip_bytes)
    md5_hash = hashlib.md5(zip_bytes).hexdigest()

    oss_path = f"fit_zip/{guid}/{md5_hash}.zip"
    oss_client = build_oss_client(region, access_token)
    if filesize <= SIMPLE_UPLOAD_MAX_BYTES:
        oss_client.put_object(zip_bytes, oss_path)
    else:
        oss_client.multipart_upload(zip_bytes, oss_path)

    sts = region_sts(region)
    upload_url = f"{team_api_base(region)}/activity/fit/import"
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
            current_user=current_user,
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
                    "accesstoken": access_token,
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
