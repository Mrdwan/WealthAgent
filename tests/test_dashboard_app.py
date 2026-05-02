"""Unit tests for src/dashboard/app.py."""

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from dashboard.app import create_app, run_dashboard


def _make_client() -> TestClient:
    """Create a TestClient (no auth needed)."""
    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi():
    app = create_app()
    assert app.title == "WealthAgent Dashboard"


# ---------------------------------------------------------------------------
# run_dashboard
# ---------------------------------------------------------------------------


def test_run_dashboard_calls_uvicorn(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_port", 8080)
    mock_run = MagicMock()
    with patch("dashboard.app.create_app") as mock_create_app:
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app
        with patch("uvicorn.run", mock_run):
            run_dashboard()
    mock_run.assert_called_once_with(mock_app, host="0.0.0.0", port=8080, log_level="info")
