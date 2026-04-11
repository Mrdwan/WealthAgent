"""Unit tests for src/dashboard/app.py."""

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from dashboard.app import create_app, run_dashboard


@pytest.fixture()
def client(monkeypatch):
    """TestClient with dashboard_secret_key set and redirects not followed."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.dashboard_enabled", True)
    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


def test_root_redirects_to_reports(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/reports"


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "password" in response.text


def test_login_success_sets_cookie(client):
    response = client.post("/login", data={"password": "testpassword"})
    assert response.status_code == 302
    assert "wa_session" in response.cookies


def test_login_failure_shows_error(client):
    response = client.post("/login", data={"password": "wrongpassword"})
    assert response.status_code == 401
    assert "Invalid" in response.text


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_cookie(client):
    # First login to get a session
    login_resp = client.post("/login", data={"password": "testpassword"})
    assert "wa_session" in login_resp.cookies

    # Now logout
    response = client.get("/logout")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# run_dashboard
# ---------------------------------------------------------------------------


def test_run_dashboard_calls_uvicorn(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.dashboard_port", 8080)
    mock_run = MagicMock()
    with patch("dashboard.app.create_app") as mock_create_app:
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app
        with patch("uvicorn.run", mock_run):
            run_dashboard()
    mock_run.assert_called_once_with(mock_app, host="0.0.0.0", port=8080, log_level="info")
