import urllib3
import json
import oss2
import os
import certifi

from oss2 import SizedFileAdapter, determine_part_size
from oss2.models import PartInfo
from app.services.oss.sts_token_error import StsTokenError
from app.utils.coros_oss_credients_utils import decode
from app.utils.coros_web_config import build_proxy_url, COOKIE_NAME

LEGACY_STS_URL = "https://faq.coros.com/openapi/oss/sts"


class AliOssClient:
    def __init__(self, access_token=None, bucket="coros-oss", service="aliyun", app_id="1660188068672619112", sign="9AD4AA35AAFEE6BB1E847A76848D58DF", v=2, region=2):
        self.bucket = bucket
        self.service = service
        self.app_id = app_id
        self.sign = sign
        self.access_token = access_token
        self.region = region
        self.security_token = None
        self.access_key_id = None
        self.access_key_secret = None
        self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
        self.client = None
        self.v = v
        self.initClient()

    def _request_candidates(self):
        """按优先级给出取凭证的请求：先 v2（Training Hub 同域代理），失败再回退旧的 v1 端点。"""
        candidates = []
        if self.access_token:
            candidates.append((
                build_proxy_url(self.region, self.bucket, self.service, self.v),
                {"Cookie": f"{COOKIE_NAME}={self.access_token}", "Accept": "application/json"},
            ))
        candidates.append((
            f"{LEGACY_STS_URL}?bucket={self.bucket}&service={self.service}"
            f"&app_id={self.app_id}&sign={self.sign}&v={self.v}",
            {"Accept": "application/json"},
        ))
        return candidates

    def _fetch_credentials(self):
        """依次尝试各个端点，返回 (credentials, v)；全部失败时把上游原因带进异常。"""
        errors = []
        for url, headers in self._request_candidates():
            try:
                response = self.req.request('GET', url, headers=headers, timeout=20)
                payload = json.loads(response.data)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url} 请求异常: {e}")
                continue

            if payload.get("code") != 200:
                errors.append(f"{url} code={payload.get('code')} msg={payload.get('msg')}")
                continue

            data = payload.get("data") or {}
            credentials = data.get("credentials")
            if not credentials:
                errors.append(f"{url} 响应缺少 credentials")
                continue
            return credentials, data.get("v", self.v)

        raise StsTokenError("获取阿里云OSS STS Token异常: " + " | ".join(errors))

    def initClient(self):
        credentials, self.v = self._fetch_credentials()
        credentials_json = decode(credentials)

        self.security_token = credentials_json["SecurityToken"]
        self.access_key_id = credentials_json["AccessKeyId"]
        self.access_key_secret = credentials_json["AccessKeySecret"]

        region_id = credentials_json.get("Region") or "oss-cn-beijing"
        endpoint = f"https://{region_id}.aliyuncs.com"
        bucket_name = credentials_json.get("Bucket") or self.bucket

        auth = oss2.StsAuth(self.access_key_id, self.access_key_secret, self.security_token)
        self.client = oss2.Bucket(auth, endpoint, bucket_name)
    
    def multipart_upload(self, filePath, fileName):
        key = f"{fileName}"
        init_multipart_upload_result = self.client.init_multipart_upload(key)
        if init_multipart_upload_result.status != 200:
            raise AliOssError("初始化阿里云分片上传异常")
        upload_id = init_multipart_upload_result.upload_id
        total_size = os.path.getsize(filePath)
        # determine_part_size方法用于确定分片大小。
        part_size = determine_part_size(total_size, preferred_size=1024 * 1024)
        parts = []

        # 逐个上传分片。
        with open(filePath, 'rb') as fileobj:
            part_number = 1
            offset = 0
            while offset < total_size:
                num_to_upload = min(part_size, total_size - offset)
                # 调用SizedFileAdapter(fileobj, size)方法会生成一个新的文件对象，重新计算起始追加位置。
                result = self.client.upload_part(key, upload_id, part_number,
                                            SizedFileAdapter(fileobj, num_to_upload))
                parts.append(PartInfo(part_number, result.etag))

                offset += num_to_upload
                part_number += 1

        # 完成分片上传。
        # 如需在完成分片上传时设置相关Headers，请参考如下示例代码。
        headers = dict()
        # 设置文件访问权限ACL。此处设置为OBJECT_ACL_PRIVATE，表示私有权限。
        # headers["x-oss-object-acl"] = oss2.OBJECT_ACL_PRIVATE
        r = self.client.complete_multipart_upload(key, upload_id, parts, headers=headers)
        if r.status == 200:
            # print(f"上传成功，文件Key: {key}")
            return key
        else:
            raise AliOssError(f"完成分片上传失败, status={r.status}")

class AliOssError(Exception):
    def __init__(self, status):
        """Initialize."""
        super(AliOssError, self).__init__(status)
        self.status = status