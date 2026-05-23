
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.models.base_activity import BaseActivity
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import garmin_service,coros_service

def pull_full_activities(connect_id:int, db: Session, current_user: User, incremental: bool = False) -> dict:
  """全量/增量同步。"""
  base_connect = db.query(BaseConnect).filter(BaseConnect.id == connect_id).first()
  if not base_connect:
   return {"status": "error", "message": "未找到授权配置，请先登录获取授权。"}
  platform = base_connect.source_type
  if platform == "garmin":
      # 调用 Garmin 国际区同步接口
      return garmin_service.pull_full_garmin(region="GLOBAL", current_user=current_user, db=db,incremental=incremental)
  elif platform == "garmin_cn":
      # 调用 Garmin 中国区同步接口
      return garmin_service.pull_full_garmin(region="CN", current_user=current_user, db=db,incremental=incremental)
  elif platform == "coros":
      # 调用 Coros 同步接口
      return coros_service.pull_full_coros(current_user=current_user, db=db,incremental=incremental)
  else:
      raise HTTPException(status_code=400, detail="不支持的平台类型")

def download_activity(id:int,db: Session, current_user: User):
  """下载文件"""
  if not id:
    return {"status": "error", "message": "缺少 activity_id 参数，无法下载。"}
  base_activity = db.query(BaseActivity).filter(BaseActivity.id == id).first()
  if not base_activity:
    return {"status": "error", "message": "未找到对应的活动记录"}
  if base_activity and base_activity.source_provider == "coros":
      file_response, filename = coros_service.get_coros_activity_download_info(db, current_user, id)
      return StreamingResponse(
        file_response.iter_content(chunk_size=8192),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
  elif base_activity and base_activity.source_provider == "garmin":
    # 1. 获取 Response 对象（此时连接仍处于 open 状态）
    file_response, filename = garmin_service.get_garmin_activity_download_info(db, current_user, id)
    
    # 2. 定义生成器，确保在传输完成后关闭连接
    def stream_contents():
        try:
            # 这里的 .iter_content 是 requests 对象的方法
            for chunk in file_response.iter_content(chunk_size=8192):
                yield chunk
        finally:
            # 无论传输成功还是客户端断开，都关闭与佳明的连接
            file_response.close()

    return StreamingResponse(
        stream_contents(),
        media_type="application/zip", # 佳明原始文件通常是压缩包
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
  return None

def upload_activity_to_target(id:int, target_connect_id:int, db: Session, current_user: User):
  """把运动数据同步到指定账号"""
  return None
