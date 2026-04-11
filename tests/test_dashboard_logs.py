"""Unit tests for src/dashboard/routes_logs.py."""

import pytest
from starlette.testclient import TestClient

from dashboard.app import create_app
from dashboard.auth import create_session_token


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Authenticated TestClient for dashboard log routes, with tmp_path as log_dir."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    app = create_app()
    test_client = TestClient(app, follow_redirects=False)
    token = create_session_token()
    test_client.cookies.set("wa_session", token)
    return test_client, tmp_path


@pytest.fixture()
def unauth_client(monkeypatch, tmp_path):
    """Unauthenticated TestClient."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_logs_list_requires_auth(unauth_client):
    response = unauth_client.get("/logs")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_log_view_requires_auth(unauth_client):
    response = unauth_client.get("/logs/11-04-2026.log")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------


def test_logs_list_empty_dir(client):
    test_client, _ = client
    response = test_client.get("/logs")
    assert response.status_code == 200
    assert "No log files" in response.text


def test_logs_list_nonexistent_dir(monkeypatch, tmp_path):
    """log_dir does not exist → 200, 'No log files'."""
    nonexistent = tmp_path / "does_not_exist"
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    monkeypatch.setattr("config.settings.settings.log_dir", nonexistent)
    app = create_app()
    test_client = TestClient(app, follow_redirects=False)
    token = create_session_token()
    test_client.cookies.set("wa_session", token)
    response = test_client.get("/logs")
    assert response.status_code == 200
    assert "No log files" in response.text


def test_logs_list_shows_valid_files(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("line one\n")
    (log_dir / "10-04-2026.log").write_text("line two\n")
    response = test_client.get("/logs")
    assert response.status_code == 200
    assert "11-04-2026.log" in response.text
    assert "10-04-2026.log" in response.text


def test_logs_list_sorted_newest_first(client):
    test_client, log_dir = client
    (log_dir / "09-04-2026.log").write_text("old\n")
    (log_dir / "11-04-2026.log").write_text("newest\n")
    (log_dir / "10-04-2026.log").write_text("middle\n")
    response = test_client.get("/logs")
    assert response.status_code == 200
    text = response.text
    pos_newest = text.index("11-04-2026.log")
    pos_middle = text.index("10-04-2026.log")
    pos_old = text.index("09-04-2026.log")
    assert pos_newest < pos_middle < pos_old


def test_logs_list_ignores_invalid_filenames(client):
    test_client, log_dir = client
    (log_dir / "debug.log").write_text("ignored\n")
    (log_dir / "not-a-log.txt").write_text("also ignored\n")
    (log_dir / "11-04-2026.log").write_text("valid\n")
    response = test_client.get("/logs")
    assert response.status_code == 200
    assert "debug.log" not in response.text
    assert "not-a-log.txt" not in response.text
    assert "11-04-2026.log" in response.text


# ---------------------------------------------------------------------------
# View page
# ---------------------------------------------------------------------------


def test_log_view_renders_content(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("INFO something happened\nERROR oh no\n")
    response = test_client.get("/logs/11-04-2026.log")
    assert response.status_code == 200
    assert "INFO something happened" in response.text
    assert "ERROR oh no" in response.text


def test_log_view_shows_line_count(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("a\nb\nc\nd\ne\n")
    response = test_client.get("/logs/11-04-2026.log")
    assert response.status_code == 200
    assert "5 lines" in response.text


def test_log_view_not_found(client):
    test_client, _ = client
    response = test_client.get("/logs/11-04-2026.log")
    assert response.status_code == 404


def test_log_view_invalid_filename(client):
    test_client, _ = client
    response = test_client.get("/logs/debug.log")
    assert response.status_code == 404


def test_log_view_rejects_path_traversal(client):
    test_client, _ = client
    response = test_client.get("/logs/../etc/passwd")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# _list_log_files helper — bad date values in otherwise valid-pattern filename
# ---------------------------------------------------------------------------


def test_list_log_files_handles_bad_date(tmp_path, monkeypatch):
    """A filename matching the pattern but with invalid date values is skipped."""
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    # month 13 is invalid — should be skipped gracefully
    (tmp_path / "01-13-2026.log").write_text("bad date\n")
    (tmp_path / "11-04-2026.log").write_text("good\n")

    from dashboard.routes_logs import _list_log_files

    result = _list_log_files()
    filenames = [f["filename"] for f in result]
    assert "01-13-2026.log" not in filenames
    assert "11-04-2026.log" in filenames
