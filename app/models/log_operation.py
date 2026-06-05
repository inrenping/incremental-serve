from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    BigInteger,
    DateTime,
    ForeignKey,
    func,
)
from app.db.session import Base


class OperationLog(Base):
    """
    用户操作日志实体类
    映射表名: public.t_log_operation
    """

    __tablename__ = "t_log_operation"

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键，自增ID"
    )
    user_id = Column(
        Integer,
        ForeignKey("t_users.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 t_users 表的外键",
    )
    log_type = Column(
        String(50), nullable=False, comment="操作类型，例如 CREATE/UPDATE/DELETE/LOGIN"
    )
    module_name = Column(String(100), nullable=True, comment="模块或功能名称")
    op_desc = Column(Text, nullable=True, comment="操作描述，简要说明用户做了什么")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="操作时间，默认当前时间",
    )

    def __repr__(self):
        return f"<OperationLog(id={self.id}, log_type='{self.log_type}', op_desc='{self.op_desc}')>"
