from sqlalchemy.orm import Session
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import coros_service, garmin_service, suunto_service
from fastapi import HTTPException


def get_connects(db: Session, current_user: User):
    """获取当前用户下的账号连接配置。"""
    connect_configs = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.is_active == True)
        .order_by(BaseConnect.sort_order.asc(), BaseConnect.created_at.desc())
        .all()
    )
    return connect_configs


def reorder_connects(db: Session, current_user: User, ordered_ids: list[int]):
    """按前端拖拽提交的 id 顺序重写 sort_order。

    只接受属于当前用户的 id，且忽略不在列表里的 id（避免越权改他人账号）。
    返回重排后的完整账号列表。
    """
    if not ordered_ids:
        return []

    owned = (
        db.query(BaseConnect)
        .filter(
            BaseConnect.user_id == current_user.id,
            BaseConnect.id.in_(ordered_ids),
        )
        .all()
    )
    owned_map = {c.id: c for c in owned}

    # 按提交的顺序逐个写入位次。提交的重复 id 会取到最后一个位次，
    # 属于脏数据但不会破坏其他记录的顺序。
    for position, connect_id in enumerate(ordered_ids):
        target = owned_map.get(connect_id)
        if target is not None:
            target.sort_order = position

    db.commit()

    return get_connects(db, current_user)


def get_connect(id: int, db: Session, current_user: User):
    """获取账号连接配置。"""
    connect_configs = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == id)
        .first()
    )
    return connect_configs


def resolve_owned_connect(
    db: Session, current_user: User, connect_id: int
) -> tuple[BaseConnect | None, str | None, str | None]:
    """按 id 查连接并校验归属，用于拦截跨用户访问（IDOR）。

    与 get_connect 的区别：get_connect 把「不存在」和「不属于本人」统一吞成 None，
    本函数显式区分失败原因，便于定位是参数填错还是越权尝试，也便于记安全审计。

    Args:
        db: 数据库会话
        current_user: 当前登录用户
        connect_id: 待校验的账号连接 ID

    Returns:
        (connect, error_message, reason)：
        - 成功时 error_message 与 reason 均为 None；
        - 失败时 reason 取值为 missing / not_found / forbidden，
          其中 forbidden 表示连接存在但不属于当前用户（越权信号，需审计）；
        - reason 为 inactive 时表示连接已停用，但**不阻断**（error_message 为 None），
          保持与历史行为一致，调用方可按需自行收紧。
    """
    if not connect_id:
        return None, "缺少账号连接参数", "missing"

    base_connect = db.query(BaseConnect).filter(BaseConnect.id == connect_id).first()
    if not base_connect:
        return None, f"账号连接 {connect_id} 不存在", "not_found"
    if base_connect.user_id != current_user.id:
        return None, f"账号连接 {connect_id} 不属于当前用户，无权访问", "forbidden"

    reason = "inactive" if base_connect.is_active is False else None
    return base_connect, None, reason


def test_connect(id: int, db: Session, current_user: User):
    """测试 token 有效性"""
    if not id:
        return {"status": "error", "message": "缺少 id 参数，无法测试。"}
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == id)
        .first()
    )
    if not base_connect:
        return {"status": "error", "message": "未找到授权配置，请先登录获取授权。"}
    elif base_connect.source_type == "coros":
        if coros_service.test_coros_token(base_connect.id, db, current_user):
            return base_connect
        else:
            return {"status": "error", "message": "coros 测试失败"}
    elif base_connect.source_type.startswith("garmin"):
        if garmin_service.test_garmin_token(base_connect.id, db, current_user):
            return base_connect
        else:
            return {"status": "error", "message": "garmin 测试失败"}
    elif base_connect.source_type == "suunto":
        if suunto_service.test_suunto_token(base_connect.id, db, current_user):
            return base_connect
        else:
            return {"status": "error", "message": "suunto 测试失败"}
    return {"status": "error", "message": "测试失败"}


def perform_login(
    id: int, email: str, password: str, region: str, db: Session, current_user: User, source_type: str = None
) -> BaseConnect:
    print(f"perform_login->region:{region } source_type:{source_type}")
    # 颂拓：source_type 显式传入，按 source_type 路由（与 region 平台选择器解耦）
    if source_type == "suunto":
        return suunto_service.perform_suunto_login(
            id=id,
            account=email,
            encrypted_password=password,
            region=region,
            db=db,
            current_user=current_user,
        )
    if region == "coros":
        coros_auth = coros_service.perform_coros_login(
            id=id,
            db=db,
            current_user=current_user,
            account=email,
            encrypted_password=password,
        )
        return coros_auth

    elif region.upper() == "GLOBAL" or region.upper() == "CN":
        print(f"region:{region }")
        # 先刷新 secret_string
        updated_auth = garmin_service.get_garmin_secret_string(
            id=id,
            account=email,
            encrypted_password=password,
            region=region,
            db=db,
            current_user=current_user,
        )
        # 再刷新 access_token
        print(f"准备刷新 access_token { updated_auth.id }")
        updated_auth = garmin_service.refresh_garmin_access_token(
            id=updated_auth.id, db=db, current_user=current_user
        )
        return updated_auth
    else:
        return None


def perform_relogin(connect_id: int, db: Session, current_user: User) -> dict[str, str] | type[
    BaseConnect] | BaseConnect | None:
    """刷新 Token 的操作"""
    if not connect_id:
        return {"status": "error", "message": "缺少 connect_id 参数，无法重新登录。"}
    base_connect = (
        db.query(BaseConnect)
        .filter(BaseConnect.user_id == current_user.id, BaseConnect.id == connect_id)
        .first()
    )
    if not base_connect:
        return {"status": "error", "message": "未找到授权配置，请先登录获取授权。"}
    # 如果是高驰，判断token有效性，如果无效的话，调用登录
    elif base_connect.source_type == "coros":
        if coros_service.test_coros_token(base_connect.id, db, current_user):
            return base_connect
        else:
            base_connect = coros_service.perform_coros_login(
                id=base_connect.id,
                db=db,
                current_user=current_user,
                account=base_connect.account,
                encrypted_password=base_connect.encrypted_password,
            )
            return base_connect
    # 如果是佳明，先判断 token 有效性，如果无效的话，获取 secret_string 刷新登录，如果刷新不成功，则用邮箱密码重新登录。
    elif base_connect.source_type.startswith("garmin"):
        if garmin_service.test_garmin_token(base_connect.id, db, current_user):
            return base_connect
        else:
            try:
                # 通过 secret_string 来刷新认证
                base_connect = garmin_service.refresh_garmin_secret_string(
                    base_connect.id, db, current_user
                )
                return base_connect
            except HTTPException:
                base_connect = garmin_service.refresh_garmin_access_token(
                    base_connect.id, db, current_user
                )
                return base_connect
    # 如果是颂拓，判断会话密钥有效性，失效则用保存的凭据（AES 密文）重新登录
    elif base_connect.source_type == "suunto":
        if suunto_service.test_suunto_token(base_connect.id, db, current_user):
            return base_connect
        else:
            base_connect = suunto_service.perform_suunto_login(
                id=base_connect.id,
                db=db,
                current_user=current_user,
                account=base_connect.account,
                encrypted_password=base_connect.encrypted_password,
                region=base_connect.region,
            )
            return base_connect
    return None
