"""Supabase Storage 客户端，使用 S3 兼容接口"""

import os
import boto3
from boto3.s3.transfer import TransferConfig
from typing import Optional


class SupabaseStorageClient:
    """Supabase Storage 客户端，封装 S3 兼容操作"""

    def __init__(
        self,
        bucket: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        region: str = "us-east-1",
    ):
        """
        初始化 Supabase Storage 客户端

        Args:
            bucket: 存储桶名称（从环境变量 SUPABASE_STORAGE_BUCKET 读取）
            endpoint_url: Supabase Storage S3 兼容端点（从环境变量 SUPABASE_STORAGE_ENDPOINT 读取）
            access_key_id: 访问密钥 ID（从环境变量 SUPABASE_ACCESS_KEY_ID 读取）
            secret_access_key: 访问密钥（从环境变量 SUPABASE_SECRET_ACCESS_KEY 读取）
            region: AWS 区域
        """
        self.bucket = bucket or os.getenv("SUPABASE_STORAGE_BUCKET")
        self.endpoint_url = endpoint_url or os.getenv("SUPABASE_STORAGE_ENDPOINT")
        self.access_key_id = access_key_id or os.getenv("SUPABASE_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.getenv(
            "SUPABASE_SECRET_ACCESS_KEY"
        )
        self.region = (
            region
            if region != "us-east-1"
            else os.getenv("SUPABASE_STORAGE_REGION", "us-east-1")
        )

        if not self.bucket:
            raise ValueError("未配置 SUPABASE_STORAGE_BUCKET 环境变量")
        if not self.endpoint_url:
            raise ValueError("未配置 SUPABASE_STORAGE_ENDPOINT 环境变量")
        if not self.access_key_id:
            raise ValueError("未配置 SUPABASE_ACCESS_KEY_ID 环境变量")
        if not self.secret_access_key:
            raise ValueError("未配置 SUPABASE_SECRET_ACCESS_KEY 环境变量")
        self.client = self._create_client()

    def _create_client(self):
        """创建 S3 客户端"""
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
        )

    def object_exists(self, key: str) -> bool:
        """
        检查对象是否存在

        Args:
            key: 对象路径

        Returns:
            True 存在，False 不存在
        """
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                return False
            raise

    def get_object(self, key: str) -> bytes:
        """
        获取对象内容

        Args:
            key: 对象路径

        Returns:
            文件内容 bytes

        Raises:
            Exception: 对象不存在时抛出异常
        """
        if not self.object_exists(key):
            raise Exception(f"对象不存在: {key}")
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def upload_file(self, file_path: str, key: str) -> bool:
        """
        上传文件

        Args:
            file_path: 本地文件路径
            key: 对象路径

        Returns:
            True 上传成功
        """
        config = TransferConfig(
            multipart_threshold=1024 * 1024 * 5,  # 5MB
            max_concurrency=4,
            multipart_chunksize=1024 * 1024 * 5,
            use_threads=True,
        )
        self.client.upload_file(
            file_path,
            Bucket=self.bucket,
            Key=key,
            Config=config,
        )
        return True

    def upload_bytes(self, data: bytes, key: str) -> bool:
        """
        上传字节数据

        Args:
            data: 文件内容
            key: 对象路径

        Returns:
            True 上传成功，False 上传失败
        """
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
            )
            return True
        except Exception as e:
            print(f"上传文件到 Supabase Storage 失败: {key}, 错误: {e}")
            return False
