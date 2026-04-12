from sqlalchemy import Column, String, Text, BigInteger, Integer, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# 假设你使用的是项目中统一的 Base
from app.db.session import Base

class SysLog(Base):
    """
    系统操作日志实体类
    映射表名: blunt.t_sys_log
    """
    __tablename__ = "t_sys_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    user_id = Column(String(100), nullable=True, comment="用户ID")
    user_name = Column(String(100), nullable=True, comment="用户名")
    log_type = Column(String(50), nullable=False, comment="日志类型")
    module_name = Column(String(100), nullable=True, comment="模块名称")
    op_desc = Column(Text, nullable=True, comment="操作描述")
    req_url = Column(Text, nullable=True, comment="请求地址")
    req_method = Column(String(10), nullable=True, comment="请求方法")
    req_params = Column(JSONB, nullable=True, comment="请求参数")
    ip_address = Column(INET, nullable=True, comment="IP地址")
    user_agent = Column(Text, nullable=True, comment="浏览器标识")
    duration_ms = Column(Integer, nullable=False, comment="耗时(毫秒)")
    created_at = Column(DateTime(timezone=True),nullable=False,comment="创建时间")
    resp_data = Column(String, nullable=True, comment="响应数据")

    def __repr__(self):
        return f"<SysLog(id={self.id}, log_type='{self.log_type}', op_desc='{self.op_desc}')>"