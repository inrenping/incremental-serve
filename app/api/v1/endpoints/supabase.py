import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.supabase_file import SupabaseFile
from app.services.oss.supabase_storage_client import SupabaseStorageClient

router = APIRouter()


def _get_supabase_client() -> SupabaseStorageClient:
    """获取 Supabase Storage 客户端实例"""
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET")
    endpoint = os.getenv("SUPABASE_STORAGE_ENDPOINT")
    access_key = os.getenv("SUPABASE_ACCESS_KEY_ID")
    secret_key = os.getenv("SUPABASE_SECRET_ACCESS_KEY")
    region = os.getenv("SUPABASE_STORAGE_REGION", "us-east-1")

    return SupabaseStorageClient(
        bucket=bucket,
        endpoint_url=endpoint,
        access_key_id=access_key,
        secret_access_key=secret_key,
        region=region,
    )


@router.post("/sync")
def sync_supabase_files(
    prefix: str = Query("", description="文件前缀过滤，为空则遍历全部"),
    mode: str = Query(
        "incremental",
        description="同步模式: full=全量（清空后重建）, incremental=增量（仅更新变化的文件）",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    同步 Supabase 存储桶中的文件列表到本地数据库。

    - full: 先删除匹配前缀的所有本地记录，再从 Supabase 全量拉取写入，能清理已删除的文件。
    - incremental: 基于 key 做 upsert，仅插入新文件或更新 etag 变化的文件，不删除本地已有的旧记录。
    """
    if mode not in ("full", "incremental"):
        raise HTTPException(
            status_code=400, detail="mode 参数必须为 full 或 incremental"
        )

    try:
        client = _get_supabase_client()
    except ValueError as e:
        raise HTTPException(
            status_code=500, detail=f"Supabase 客户端初始化失败: {str(e)}"
        )

    try:
        objects = client.list_objects(prefix=prefix)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"遍历 Supabase 文件失败: {str(e)}")

    bucket = client.bucket
    inserted = 0
    updated = 0
    deleted = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    # 全量模式：先清除本地匹配前缀的旧记录
    if mode == "full":
        delete_query = db.query(SupabaseFile)
        if prefix:
            delete_query = delete_query.filter(SupabaseFile.key.startswith(prefix))
        deleted = delete_query.delete(synchronize_session="fetch")

    # 收集本次 Supabase 中的所有 key，用于增量模式下清理已删除的远程文件
    remote_keys = set()

    for obj in objects:
        key = obj["key"]
        # 跳过目录标记（以 / 结尾且大小为 0 的 key）
        if key.endswith("/") and obj["size"] == 0:
            continue

        remote_keys.add(key)
        name = key.rsplit("/", 1)[-1] if "/" in key else key

        existing = db.query(SupabaseFile).filter(SupabaseFile.key == key).first()
        if existing:
            # 增量模式下，etag 未变化则跳过
            if mode == "incremental" and existing.etag == obj["etag"]:
                skipped += 1
                continue
            existing.size = obj["size"]
            existing.content_type = obj["content_type"] or existing.content_type
            existing.last_modified = obj["last_modified"]
            existing.etag = obj["etag"]
            existing.bucket = bucket
            existing.synced_at = now
            updated += 1
        else:
            new_file = SupabaseFile(
                key=key,
                name=name,
                size=obj["size"],
                content_type=obj["content_type"] or "",
                last_modified=obj["last_modified"],
                etag=obj["etag"],
                bucket=bucket,
                synced_at=now,
            )
            db.add(new_file)
            inserted += 1

    # 增量模式：清理本地存在但远程已删除的文件
    if mode == "incremental" and remote_keys:
        stale_query = db.query(SupabaseFile).filter(~SupabaseFile.key.in_(remote_keys))
        if prefix:
            stale_query = stale_query.filter(SupabaseFile.key.startswith(prefix))
        stale_count = stale_query.delete(synchronize_session="fetch")
        deleted += stale_count

    db.commit()

    return {
        "mode": mode,
        "total": len(objects),
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "bucket": bucket,
    }


@router.get("/files")
def list_supabase_files(
    prefix: str = Query("", description="按文件 key 前缀过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=500, description="每页数量"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    分页查询本地数据库中的 Supabase 文件列表。
    """
    query = db.query(SupabaseFile)
    if prefix:
        query = query.filter(SupabaseFile.key.startswith(prefix))
    query = query.order_by(SupabaseFile.key)

    total = query.count()
    files = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": [
            {
                "id": f.id,
                "key": f.key,
                "name": f.name,
                "size": f.size,
                "content_type": f.content_type,
                "last_modified": (
                    f.last_modified.isoformat() if f.last_modified else None
                ),
                "etag": f.etag,
                "bucket": f.bucket,
                "synced_at": f.synced_at.isoformat() if f.synced_at else None,
            }
            for f in files
        ],
    }
