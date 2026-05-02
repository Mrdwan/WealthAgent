"""Unit tests for src/dashboard/routes_alerts.py (JSON API)."""

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client():
    """TestClient for dashboard alerts routes (no auth needed)."""
    from dashboard.app import create_app

    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /api/alerts — list
# ---------------------------------------------------------------------------


def test_alerts_list_empty(client):
    """No alerts → returns empty list."""
    response = client.get("/api/alerts")
    assert response.status_code == 200
    data = response.json()
    assert data["alerts"] == []


def test_alerts_list_shows_recent(client):
    """2 recent alerts appear in the response."""
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

    response = client.get("/api/alerts")
    assert response.status_code == 200
    data = response.json()
    tickers = [a["ticker"] for a in data["alerts"]]
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_alerts_list_excludes_old(client):
    """Alert from 31 days ago is not shown."""
    old_at = (datetime.now() - timedelta(days=31)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (old_at, "OLD", "price_drop", "very old"),
        )

    response = client.get("/api/alerts")
    assert response.status_code == 200
    data = response.json()
    tickers = [a["ticker"] for a in data["alerts"]]
    assert "OLD" not in tickers


# ---------------------------------------------------------------------------
# GET /api/alerts/config — configuration
# ---------------------------------------------------------------------------


def test_alerts_config_shows_defaults(client, monkeypatch):
    """No DB config → shows values from settings defaults."""
    monkeypatch.setattr("config.settings.settings.alert_drop_pct", 10.0)
    monkeypatch.setattr("config.settings.settings.stop_loss_pct", 8.0)
    monkeypatch.setattr("config.settings.settings.dividend_yield_max", 2.0)

    response = client.get("/api/alerts/config")
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["alert_drop_pct"] == "10.0"
    assert data["config"]["stop_loss_pct"] == "8.0"
    assert data["config"]["dividend_yield_max"] == "2.0"


def test_alerts_config_shows_saved_values(client):
    """After saving to DB, the endpoint shows the DB values."""
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

    response = client.get("/api/alerts/config")
    assert response.status_code == 200
    data = response.json()
    assert data["config"]["alert_drop_pct"] == "15.0"
    assert data["config"]["stop_loss_pct"] == "12.0"
    assert data["config"]["dividend_yield_max"] == "3.5"


# ---------------------------------------------------------------------------
# POST /api/alerts/config — update thresholds
# ---------------------------------------------------------------------------


def test_update_alerts_config_saves(client):
    """POST saves values to DB and returns ok status."""
    response = client.post(
        "/api/alerts/config",
        json={
            "alert_drop_pct": "12.5",
            "stop_loss_pct": "9.0",
            "dividend_yield_max": "4.0",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

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
