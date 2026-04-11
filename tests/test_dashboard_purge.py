"""Unit tests for src/dashboard/routes_purge.py."""

from datetime import date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Authenticated TestClient for dashboard purge routes, with tmp_path as log_dir."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    from dashboard.app import create_app
    from dashboard.auth import create_session_token

    app = create_app()
    c = TestClient(app, follow_redirects=False)
    token = create_session_token()
    c.cookies.set("wa_session", token)
    return c, tmp_path


@pytest.fixture()
def unauth_client(monkeypatch, tmp_path):
    """Unauthenticated TestClient."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    from dashboard.app import create_app

    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_purge_page_requires_auth(unauth_client):
    response = unauth_client.get("/purge")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_purge_logs_action_requires_auth(unauth_client):
    response = unauth_client.post("/purge/logs", data={"older_than_days": "30"})
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_purge_reports_action_requires_auth(unauth_client):
    response = unauth_client.post("/purge/reports")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# GET /purge — page renders
# ---------------------------------------------------------------------------


def test_purge_page_renders(client):
    test_client, _ = client
    response = test_client.get("/purge")
    assert response.status_code == 200
    assert "Purge" in response.text


# ---------------------------------------------------------------------------
# POST /purge/logs — log file deletion
# ---------------------------------------------------------------------------


def test_purge_logs_deletes_old_files(client):
    """2 old files and 1 recent file — only 2 should be deleted."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    recent_date = date.today() - timedelta(days=5)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old1\n")
    (log_dir / f"{(old_date + timedelta(days=1)).strftime('%d-%m-%Y')}.log").write_text("old2\n")
    (log_dir / f"{recent_date.strftime('%d-%m-%Y')}.log").write_text("recent\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "30"})
    assert response.status_code == 200

    remaining = list(log_dir.iterdir())
    assert len(remaining) == 1
    assert remaining[0].name == f"{recent_date.strftime('%d-%m-%Y')}.log"


def test_purge_logs_returns_count(client):
    """Response shows 'Deleted 2 log files.' when 2 files are removed."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old1\n")
    (log_dir / f"{(old_date + timedelta(days=1)).strftime('%d-%m-%Y')}.log").write_text("old2\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "30"})
    assert response.status_code == 200
    assert "Deleted 2 log files." in response.text


def test_purge_logs_singular(client):
    """1 file deleted → 'Deleted 1 log file.' (no trailing 's')."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "30"})
    assert response.status_code == 200
    assert "Deleted 1 log file." in response.text


def test_purge_logs_nonexistent_dir(monkeypatch, tmp_path):
    """log_dir doesn't exist → 0 deleted, no error."""
    nonexistent = tmp_path / "does_not_exist"
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", nonexistent)
    from dashboard.app import create_app
    from dashboard.auth import create_session_token

    app = create_app()
    c = TestClient(app, follow_redirects=False)
    token = create_session_token()
    c.cookies.set("wa_session", token)

    response = c.post("/purge/logs", data={"older_than_days": "30"})
    assert response.status_code == 200
    assert "Deleted 0 log files." in response.text


def test_purge_logs_clamps_minimum_to_1(client):
    """older_than_days=0 is treated as 1 — no crash, just clamps to 1."""
    test_client, log_dir = client
    two_days_ago = date.today() - timedelta(days=2)
    (log_dir / f"{two_days_ago.strftime('%d-%m-%Y')}.log").write_text("two days ago\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "0"})
    assert response.status_code == 200
    # With older_than_days clamped to 1, cutoff = today - 1 day; a 2-day-old file IS deleted
    assert "Deleted 1 log file." in response.text


def test_purge_logs_ignores_invalid_filenames(client):
    """Files not matching DD-MM-YYYY.log are not deleted."""
    test_client, log_dir = client
    (log_dir / "debug.log").write_text("debug stuff\n")
    (log_dir / "not-a-log.txt").write_text("text file\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "1"})
    assert response.status_code == 200
    assert "Deleted 0 log files." in response.text
    # Non-matching files remain untouched
    assert (log_dir / "debug.log").exists()
    assert (log_dir / "not-a-log.txt").exists()


def test_purge_logs_skips_bad_date_in_valid_pattern(client):
    """A filename matching DD-MM-YYYY.log but with an invalid date is silently skipped."""
    test_client, log_dir = client
    # month 13 is invalid — passes the regex but fails date(int) parsing
    (log_dir / "01-13-2020.log").write_text("bad date\n")

    response = test_client.post("/purge/logs", data={"older_than_days": "1"})
    assert response.status_code == 200
    assert "Deleted 0 log files." in response.text
    assert (log_dir / "01-13-2020.log").exists()


# ---------------------------------------------------------------------------
# POST /purge/reports — expired report deletion
# ---------------------------------------------------------------------------


def test_purge_reports_deletes_expired(client):
    """Insert an expired report → purge → response shows deleted count."""
    test_client, _ = client
    expired_at = (datetime.now() - timedelta(days=1)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO reports (report_type, ticker, summary, full_content, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("analyze", "AAPL", "summary", "full content", expired_at),
        )

    response = test_client.post("/purge/reports")
    assert response.status_code == 200
    assert "Deleted 1 expired report." in response.text


def test_purge_reports_none_expired(client):
    """No expired reports → 'Deleted 0 expired reports.'"""
    test_client, _ = client
    response = test_client.post("/purge/reports")
    assert response.status_code == 200
    assert "Deleted 0 expired reports." in response.text


# ---------------------------------------------------------------------------
# POST /purge/data — pipeline data deletion
# ---------------------------------------------------------------------------


def test_purge_data_action_requires_auth(unauth_client):
    data = {
        "news_days": "28",
        "alerts_days": "28",
        "screener_days": "120",
        "fundamentals_days": "28",
    }
    response = unauth_client.post("/purge/data", data=data)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_purge_data_action_deletes_old_rows(client):
    """Old news/alert/screener/fundamentals rows are deleted; result shown in response."""
    from datetime import timedelta

    test_client, _ = client
    old_ts = (datetime.now() - timedelta(days=60)).isoformat()

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, fetched_at, processed) VALUES (?, ?, 1)",
            ("http://old.example.com", old_ts),
        )
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type) VALUES (?, ?, ?)",
            (old_ts, "AAPL", "price_drop"),
        )

    data = {"news_days": "7", "alerts_days": "7", "screener_days": "120", "fundamentals_days": "28"}
    response = test_client.post("/purge/data", data=data)
    assert response.status_code == 200
    assert "Deleted" in response.text
    assert "news" in response.text


def test_purge_data_action_clamps_minimum_to_1(client):
    """days=0 inputs are clamped to 1 — no crash."""
    test_client, _ = client
    response = test_client.post(
        "/purge/data",
        data={"news_days": "0", "alerts_days": "0", "screener_days": "0", "fundamentals_days": "0"},
    )
    assert response.status_code == 200
    assert "Deleted" in response.text
