"""统一的存储服务接口，基于 Supabase Storage"""

from typing import Optional
from app.services.oss.supabase_storage_client import SupabaseStorageClient


def get_storage_client() -> SupabaseStorageClient:
    """获取 Supabase Storage 客户端实例

    Returns:
        SupabaseStorageClient 实例
    """
    return SupabaseStorageClient()


def check_fit_file_exists(oss_key: str) -> bool:
    """检查 FIT 文件是否存在于存储中

    Args:
        oss_key: 对象路径

    Returns:
        True 存在，False 不存在
    """
    try:
        client = get_storage_client()
        return client.object_exists(oss_key)
    except Exception as e:
        print(f"检查存储文件是否存在失败: {e}")
        return False


def download_fit_file(oss_key: str) -> Optional[bytes]:
    """从存储下载 FIT 文件

    Args:
        oss_key: 对象路径

    Returns:
        文件内容 bytes，失败返回 None
    """
    try:
        client = get_storage_client()
        return client.get_object(oss_key)
    except Exception as e:
        print(f"从存储下载文件失败: {e}")
        return None


def upload_fit_file(file_path: str, oss_key: str) -> bool:
    """上传 FIT 文件到存储

    Args:
        file_path: 本地文件路径
        oss_key: 对象路径

    Returns:
        True 上传成功，False 上传失败
    """
    try:
        client = get_storage_client()
        return client.upload_file(file_path, oss_key)
    except Exception as e:
        print(f"上传文件到存储失败: {e}")
        return False


def upload_fit_bytes(data: bytes, oss_key: str) -> bool:
    """上传 FIT 文件字节数据到存储

    Args:
        data: 文件内容
        oss_key: 对象路径

    Returns:
        True 上传成功，False 上传失败
    """
    try:
        client = get_storage_client()
        return client.upload_bytes(data, oss_key)
    except Exception as e:
        print(f"上传文件到存储失败: {e}")
        return False


def generate_fit_oss_key(activity_id: int) -> str:
    """生成 FIT 文件的存储路径

    Args:
        activity_id: 活动 ID（全局唯一）

    Returns:
        对象路径，格式: {activity_id}.fit
    """
    return f"{activity_id}.fit"
