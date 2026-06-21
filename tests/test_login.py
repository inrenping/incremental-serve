import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session

from app.main import app
from app.models.user import User
from app.models.base_connect import BaseConnect
from app.api.v1.endpoints.base import ActionType

client = TestClient(app)


@pytest.fixture
def mock_db_session():
    """Mock database session fixture."""
    return Mock(spec=Session)


@pytest.fixture
def mock_current_user():
    """Mock current user fixture."""
    user = User()
    user.id = 1
    return user


def test_login_add_success(mock_db_session, mock_current_user):
    """Test login with action=add when account doesn't exist."""
    # Mock database query to return None (account doesn't exist)
    mock_query = Mock()
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = None

    with patch("app.db.session.get_db", return_value=iter([mock_db_session])), patch(
        "app.core.security.get_current_user", return_value=mock_current_user
    ), patch.object(mock_db_session, "query", return_value=mock_query), patch(
        "app.services.base_connect_service.perform_login"
    ) as mock_perform_login:

        # Mock successful login
        mock_base_connect = Mock(spec=BaseConnect)
        mock_base_connect.id = 1
        mock_perform_login.return_value = mock_base_connect

        response = client.post(
            "/api/v1/base/login",
            json={
                "id": 1,
                "region": "CN",
                "email": "test@example.com",
                "password": "password123",
                "action": "add",
                "master": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == 1


def test_login_add_duplicate_account(mock_db_session, mock_current_user):
    """Test login with action=add when account already exists."""
    # Mock database query to return existing account
    mock_query = Mock()
    mock_query.filter.return_value = mock_query
    mock_existing_connect = Mock(spec=BaseConnect)
    mock_query.first.return_value = mock_existing_connect

    with patch("app.db.session.get_db", return_value=iter([mock_db_session])), patch(
        "app.core.security.get_current_user", return_value=mock_current_user
    ), patch.object(mock_db_session, "query", return_value=mock_query):

        response = client.post(
            "/api/v1/base/login",
            json={
                "id": 1,
                "region": "CN",
                "email": "existing@example.com",
                "password": "password123",
                "action": "add",
                "master": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "该账号已存在" in data["message"]


def test_login_update_success(mock_db_session, mock_current_user):
    """Test login with action=update when account exists."""
    # Mock database query to return existing account
    mock_query = Mock()
    mock_query.filter.return_value = mock_query
    mock_existing_connect = Mock(spec=BaseConnect)
    mock_query.first.return_value = mock_existing_connect

    with patch("app.db.session.get_db", return_value=iter([mock_db_session])), patch(
        "app.core.security.get_current_user", return_value=mock_current_user
    ), patch.object(mock_db_session, "query", return_value=mock_query), patch(
        "app.services.base_connect_service.perform_login"
    ) as mock_perform_login:

        # Mock successful login
        mock_base_connect = Mock(spec=BaseConnect)
        mock_base_connect.id = 1
        mock_perform_login.return_value = mock_base_connect

        response = client.post(
            "/api/v1/base/login",
            json={
                "id": 1,
                "region": "CN",
                "email": "existing@example.com",
                "password": "password123",
                "action": "update",
                "master": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"] == 1


def test_login_update_not_found(mock_db_session, mock_current_user):
    """Test login with action=update when account doesn't exist."""
    # Mock database query to return None (account doesn't exist)
    mock_query = Mock()
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = None

    with patch("app.db.session.get_db", return_value=iter([mock_db_session])), patch(
        "app.core.security.get_current_user", return_value=mock_current_user
    ), patch.object(mock_db_session, "query", return_value=mock_query):

        response = client.post(
            "/api/v1/base/login",
            json={
                "id": 1,
                "region": "CN",
                "email": "nonexistent@example.com",
                "password": "password123",
                "action": "update",
                "master": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "该账号不存在" in data["message"]


def test_login_invalid_action(mock_db_session, mock_current_user):
    """Test login with invalid action value."""
    with patch("app.db.session.get_db", return_value=iter([mock_db_session])), patch(
        "app.core.security.get_current_user", return_value=mock_current_user
    ):

        response = client.post(
            "/api/v1/base/login",
            json={
                "id": 1,
                "region": "CN",
                "email": "test@example.com",
                "password": "password123",
                "action": "invalid_action",
                "master": False,
            },
        )

        # FastAPI automatically validates Enum fields and returns 422 for invalid values
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
