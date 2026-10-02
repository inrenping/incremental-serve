"""同步链路的账号归属校验（IDOR 防护）测试。

覆盖三层：
1. base_connect_service.resolve_owned_connect 的四种失败分支；
2. run_quick_sync 在源/目标不属于当前用户时必须拒绝，且不得建立会话、不得发起上传；
3. save_task 在账号不属于当前用户时必须拒绝建任务。
"""

from unittest.mock import Mock, patch

from sqlalchemy.orm import Session

from app.api.v1.endpoints.task import SaveTaskRequest, TaskItemPayload, save_task
from app.models.base_connect import BaseConnect
from app.models.user import User
from app.services import base_connect_service, quick_sync_service


def _user(user_id: int = 1) -> User:
    user = User()
    user.id = user_id
    return user


def _connect(connect_id: int, user_id: int, is_active=True) -> BaseConnect:
    connect = BaseConnect()
    connect.id = connect_id
    connect.user_id = user_id
    connect.is_active = is_active
    return connect


def _db_with(results) -> Session:
    """构造 mock session，使每次 query().filter().first() 依次返回 results。"""
    db = Mock(spec=Session)
    db.query.return_value.filter.return_value.first.side_effect = list(results)
    return db


def test_resolve_missing_connect_id():
    db = Mock(spec=Session)
    connect, error, reason = base_connect_service.resolve_owned_connect(db, _user(), 0)
    assert connect is None
    assert reason == "missing"


def test_resolve_not_found():
    db = _db_with([None])
    connect, error, reason = base_connect_service.resolve_owned_connect(db, _user(), 7)
    assert connect is None
    assert reason == "not_found"
    assert "不存在" in error


def test_resolve_forbidden():
    db = _db_with([_connect(9, user_id=2)])
    connect, error, reason = base_connect_service.resolve_owned_connect(db, _user(1), 9)
    assert connect is None
    assert reason == "forbidden"
    assert "不属于当前用户" in error


def test_resolve_inactive_is_not_blocking():
    """已停用但归属正确的连接不阻断，仅标记 reason，保持历史行为一致。"""
    own = _connect(9, user_id=1, is_active=False)
    db = _db_with([own])
    connect, error, reason = base_connect_service.resolve_owned_connect(db, _user(1), 9)
    assert connect is own
    assert error is None
    assert reason == "inactive"


def test_resolve_ok():
    own = _connect(9, user_id=1)
    db = _db_with([own])
    connect, error, reason = base_connect_service.resolve_owned_connect(db, _user(1), 9)
    assert connect is own
    assert error is None and reason is None


def test_quick_sync_rejects_foreign_target_without_upload():
    """目标属于他人：必须报错，且不得建立平台会话（即不产生任何下载/上传请求）。"""
    db = _db_with([_connect(1, user_id=1), _connect(99, user_id=2)])
    with patch.object(
        quick_sync_service.platform_session, "build_session"
    ) as build_session, patch.object(
        quick_sync_service, "log_operation_async"
    ) as log_async:
        result = quick_sync_service.run_quick_sync(db, _user(1), 1, 99, 10)

    assert result["status"] == "error"
    assert "不属于当前用户" in result["message"]
    build_session.assert_not_called()
    log_async.assert_called_once()


def test_quick_sync_rejects_foreign_source():
    db = _db_with([_connect(88, user_id=2), _connect(2, user_id=1)])
    with patch.object(quick_sync_service, "log_operation_async"):
        result = quick_sync_service.run_quick_sync(db, _user(1), 88, 2, 10)

    assert result["status"] == "error"
    assert "不属于当前用户" in result["message"]


def test_quick_sync_same_connect_rejected():
    db = Mock(spec=Session)
    result = quick_sync_service.run_quick_sync(db, _user(1), 5, 5, 10)
    assert result["status"] == "error"
    assert "两个账号相同" in result["message"]


def test_save_task_rejects_foreign_target():
    db = _db_with([_connect(1, user_id=1), _connect(99, user_id=2)])
    request = SaveTaskRequest(hours=[8], items=[TaskItemPayload(connect_source_id=1, connect_target_id=99)])
    response = save_task(request, _user(1), db)
    assert response["status"] == "error"
    assert "目标账号" in response["message"]


def test_save_task_rejects_same_source_and_target():
    db = _db_with([_connect(1, user_id=1), _connect(1, user_id=1)])
    request = SaveTaskRequest(hours=[8], items=[TaskItemPayload(connect_source_id=1, connect_target_id=1)])
    response = save_task(request, _user(1), db)
    assert response["status"] == "error"
    assert "不能相同" in response["message"]
