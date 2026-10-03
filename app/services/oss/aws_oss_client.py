import urllib3
import json
import boto3
import certifi

from boto3.s3.transfer import TransferConfig


from app.services.oss.sts_token_error import StsTokenError
from app.utils.coros_oss_credients_utils import decode
from app.utils.coros_web_config import build_proxy_url, COOKIE_NAME

LEGACY_STS_URL = "https://faq.coros.com/openapi/oss/sts"

class AwsOssClient:
  def __init__(self, access_token=None, bucket="eu-coros", service="aws", app_id="1660188068672619112", sign="877571111A1EE5316E4B590103D4B5B3", v=2, region=3):
    self.access_token = access_token
    self.region = region
    self.bucket = bucket
    self.service = service
    self.app_id = app_id
    self.sign = sign
    self.credentials = None
    self.access_key_id = None
    self.access_key_secret = None
    self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
    self.v = v
    self.client = None
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

    raise StsTokenError("Get AWS OSS STS Token Exception: " + " | ".join(errors))

  def initClient(self):
        credentials, self.v = self._fetch_credentials()
        self.credentials = credentials
        credients_json = decode(credentials)

        # 优先用服务端返回的 Region，回退到原来的 eu-central-1
        region_name = credients_json.get("Region") or "eu-central-1"
        endpoint_url = f"https://s3.{region_name}.amazonaws.com"

        self.client = boto3.client(
            "s3",
            aws_access_key_id=credients_json["AccessKeyId"],
            aws_secret_access_key=credients_json["SecretAccessKey"],
            aws_session_token=credients_json["SessionToken"],
            endpoint_url=endpoint_url,
        )

  def multipart_upload(self, filePath, fileName):
      # 配置上传选项
      config = TransferConfig(
          multipart_threshold=1024 * 1024 * 5,  # 分片上传的阈值（5MB）
          max_concurrency=4,                   # 并发数
          multipart_chunksize=1024 * 1024 * 5,  # 分片大小（5MB）
          use_threads=True                     # 使用多线程
      )

      # 执行上传
      try:
          # print(f"Uploading {fileName} to AWS S3...")
          self.client.upload_file(
              filePath,
              Bucket=self.bucket,
              Key=f"{fileName}",
              Config=config
          )
          # print(f"File {fileName} uploaded successfully!")
      except Exception as e:
          print(f"Upload failed: {e}")