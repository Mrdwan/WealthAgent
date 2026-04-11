"""Unit tests for src/dashboard/routes_alerts.py."""

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client(monkeypatch):
    """Authenticated TestClient for dashboard alerts routes."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    from dashboard.app import create_app
    from dashboard.auth import create_session_token

    app = create_app()
    c = TestClient(app, follow_redirects=False)
    token = create_session_token()
    c.cookies.set("wa_session", token)
    return c


@pytest.fixture()
def unauth_client(monkeypatch):
    """Unauthenticated TestClient."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    from dashboard.app import create_app

    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def test_alerts_list_requires_auth(unauth_client):
    response = unauth_client.get("/alerts")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_alerts_config_requires_auth(unauth_client):
    response = unauth_client.get("/alerts/config")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_update_alerts_config_requires_auth(unauth_client):
    response = unauth_client.post(
        "/alerts/config",
        data={
            "alert_drop_pct": "10.0",
            "stop_loss_pct": "8.0",
            "dividend_yield_max": "2.0",
        },
    )
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# GET /alerts — list
# ---------------------------------------------------------------------------


def test_alerts_list_empty(client):
    """No alerts → shows empty message."""
    response = client.get("/alerts")
    assert response.status_code == 200
    assert "No alerts in the last 30 days." in response.text


def test_alerts_list_shows_recent(client):
    """2 recent alerts appear in the table."""
    now = datetime.now().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (now, "AAPL", "price_drop", "dropped 12%"),
        )
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (now, "MSFT", "news_signal", "negative news"),
        )

    response = client.get("/alerts")
    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" in response.text
    assert "price_drop" in response.text
    assert "news_signal" in response.text


def test_alerts_list_excludes_old(client):
    """Alert from 31 days ago is not shown."""
    old_at = (datetime.now() - timedelta(days=31)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (old_at, "OLD", "price_drop", "very old"),
        )

    response = client.get("/alerts")
    assert response.status_code == 200
    assert "OLD" not in response.text
    assert "No alerts in the last 30 days." in response.text


# ---------------------------------------------------------------------------
# GET /alerts/config — configuration form
# ---------------------------------------------------------------------------


def test_alerts_config_shows_defaults(client, monkeypatch):
    """No DB config → shows values from settings defaults."""
    monkeypatch.setattr("config.settings.settings.alert_drop_pct", 10.0)
    monkeypatch.setattr("config.settings.settings.stop_loss_pct", 8.0)
    monkeypatch.setattr("config.settings.settings.dividend_yield_max", 2.0)

    response = client.get("/alerts/config")
    assert response.status_code == 200
    assert "10.0" in response.text
    assert "8.0" in response.text
    assert "2.0" in response.text


def test_alerts_config_shows_saved_values(client):
    """After saving to DB, the form shows the DB values."""
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alert_config (key, value) VALUES (?, ?)",
            ("alert_drop_pct", "15.0"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO alert_config (key, value) VALUES (?, ?)",
            ("stop_loss_pct", "12.0"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO alert_config (key, value) VALUES (?, ?)",
            ("dividend_yield_max", "3.5"),
        )

    response = client.get("/alerts/config")
    assert response.status_code == 200
    assert "15.0" in response.text
    assert "12.0" in response.text
    assert "3.5" in response.text


def test_alerts_config_shows_saved_message(client):
    """GET /alerts/config?saved=1 → shows 'Settings saved.' message."""
    response = client.get("/alerts/config?saved=1")
    assert response.status_code == 200
    assert "Settings saved." in response.text


# ---------------------------------------------------------------------------
# POST /alerts/config — update thresholds
# ---------------------------------------------------------------------------


def test_update_alerts_config_saves(client):
    """POST saves values to DB and redirects with saved=1."""
    response = client.post(
        "/alerts/config",
        data={
            "alert_drop_pct": "12.5",
            "stop_loss_pct": "9.0",
            "dividend_yield_max": "4.0",
        },
    )
    assert response.status_code == 302
    assert "/alerts/config?saved=1" in response.headers["location"]

    # Verify DB was updated
    from dashboard.routes_alerts import _get_alert_config

    assert _get_alert_config("alert_drop_pct") == "12.5"
    assert _get_alert_config("stop_loss_pct") == "9.0"
    assert _get_alert_config("dividend_yield_max") == "4.0"


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_get_alert_config_not_found():
    """Key not in DB → returns None."""
    from dashboard.routes_alerts import _get_alert_config

    assert _get_alert_config("nonexistent_key") is None


def test_set_alert_config_upserts():
    """Setting same key twice — second value wins."""
    from dashboard.routes_alerts import _get_alert_config, _set_alert_config

    _set_alert_config("test_key", "first")
    _set_alert_config("test_key", "second")
    assert _get_alert_config("test_key") == "second"


def test_list_alert_configs_empty():
    """No rows in alert_config → returns empty dict."""
    from dashboard.routes_alerts import _list_alert_configs

    assert _list_alert_configs() == {}


def test_list_alert_configs_returns_all():
    """Multiple rows → full dict returned."""
    from dashboard.routes_alerts import _list_alert_configs, _set_alert_config

    _set_alert_config("k1", "v1")
    _set_alert_config("k2", "v2")
    result = _list_alert_configs()
    assert result == {"k1": "v1", "k2": "v2"}
