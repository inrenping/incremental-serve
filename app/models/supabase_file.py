from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Text
from app.db.session import Base


class SupabaseFile(Base):
    __tablename__ = "t_supabase_files"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(
        String(1024), nullable=False, unique=True, comment="文件在桶中的路径/Key"
    )
    name = Column(String(512), nullable=True, comment="文件名（从 key 中提取）")
    size = Column(BigInteger, nullable=True, comment="文件大小（字节）")
    content_type = Column(String(256), nullable=True, comment="MIME 类型")
    last_modified = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="文件在 Supabase 中的最后修改时间",
    )
    etag = Column(String(256), nullable=True, comment="文件 ETag")
    bucket = Column(String(256), nullable=False, comment="存储桶名称")
    synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="同步到本地数据库的时间",
    )
