"""Unit tests for src/dashboard/routes_purge.py (JSON API)."""

from datetime import date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient for dashboard purge routes, with tmp_path as log_dir (no auth needed)."""
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    from dashboard.app import create_app

    app = create_app()
    c = TestClient(app, follow_redirects=False)
    return c, tmp_path


# ---------------------------------------------------------------------------
# POST /api/purge/logs — log file deletion
# ---------------------------------------------------------------------------


def test_purge_logs_deletes_old_files(client):
    """2 old files and 1 recent file — only 2 should be deleted."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    recent_date = date.today() - timedelta(days=5)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old1\n")
    (log_dir / f"{(old_date + timedelta(days=1)).strftime('%d-%m-%Y')}.log").write_text("old2\n")
    (log_dir / f"{recent_date.strftime('%d-%m-%Y')}.log").write_text("recent\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 30})
    assert response.status_code == 200

    remaining = list(log_dir.iterdir())
    assert len(remaining) == 1
    assert remaining[0].name == f"{recent_date.strftime('%d-%m-%Y')}.log"


def test_purge_logs_returns_count(client):
    """Response shows deleted count when 2 files are removed."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old1\n")
    (log_dir / f"{(old_date + timedelta(days=1)).strftime('%d-%m-%Y')}.log").write_text("old2\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 30})
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 2
    assert data["type"] == "logs"


def test_purge_logs_singular(client):
    """1 file deleted → deleted count = 1."""
    test_client, log_dir = client
    old_date = date.today() - timedelta(days=60)
    (log_dir / f"{old_date.strftime('%d-%m-%Y')}.log").write_text("old\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 30})
    data = response.json()
    assert data["deleted"] == 1


def test_purge_logs_nonexistent_dir(monkeypatch, tmp_path):
    """log_dir doesn't exist → 0 deleted, no error."""
    nonexistent = tmp_path / "does_not_exist"
    monkeypatch.setattr("config.settings.settings.log_dir", nonexistent)
    from dashboard.app import create_app

    app = create_app()
    c = TestClient(app, follow_redirects=False)

    response = c.post("/api/purge/logs", json={"older_than_days": 30})
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 0


def test_purge_logs_clamps_minimum_to_1(client):
    """older_than_days=0 is treated as 1 — no crash, just clamps to 1."""
    test_client, log_dir = client
    two_days_ago = date.today() - timedelta(days=2)
    (log_dir / f"{two_days_ago.strftime('%d-%m-%Y')}.log").write_text("two days ago\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 0})
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 1


def test_purge_logs_ignores_invalid_filenames(client):
    """Files not matching DD-MM-YYYY.log are not deleted."""
    test_client, log_dir = client
    (log_dir / "debug.log").write_text("debug stuff\n")
    (log_dir / "not-a-log.txt").write_text("text file\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 1})
    data = response.json()
    assert data["deleted"] == 0
    assert (log_dir / "debug.log").exists()
    assert (log_dir / "not-a-log.txt").exists()


def test_purge_logs_skips_bad_date_in_valid_pattern(client):
    """A filename matching DD-MM-YYYY.log but with an invalid date is silently skipped."""
    test_client, log_dir = client
    (log_dir / "01-13-2020.log").write_text("bad date\n")

    response = test_client.post("/api/purge/logs", json={"older_than_days": 1})
    data = response.json()
    assert data["deleted"] == 0
    assert (log_dir / "01-13-2020.log").exists()


# ---------------------------------------------------------------------------
# POST /api/purge/reports — expired report deletion
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

    response = test_client.post("/api/purge/reports")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 1
    assert data["type"] == "reports"


def test_purge_reports_none_expired(client):
    """No expired reports → deleted = 0."""
    test_client, _ = client
    response = test_client.post("/api/purge/reports")
    data = response.json()
    assert data["deleted"] == 0


# ---------------------------------------------------------------------------
# POST /api/purge/data — pipeline data deletion
# ---------------------------------------------------------------------------


def test_purge_data_action_deletes_old_rows(client):
    """Old news/alert rows are deleted."""
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

    response = test_client.post(
        "/api/purge/data",
        json={"news_days": 7, "alerts_days": 7, "screener_days": 120, "fundamentals_days": 28},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert "news" in data["deleted"]


def test_purge_data_action_clamps_minimum_to_1(client):
    """days=0 inputs are clamped to 1 — no crash."""
    test_client, _ = client
    response = test_client.post(
        "/api/purge/data",
        json={"news_days": 0, "alerts_days": 0, "screener_days": 0, "fundamentals_days": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
