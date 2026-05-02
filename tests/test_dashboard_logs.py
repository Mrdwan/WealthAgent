"""Unit tests for src/dashboard/routes_logs.py (JSON API)."""

import pytest
from starlette.testclient import TestClient

from dashboard.app import create_app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient for dashboard log routes, with tmp_path as log_dir (no auth needed)."""
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    app = create_app()
    test_client = TestClient(app, follow_redirects=False)
    return test_client, tmp_path


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_logs_list_empty_dir(client):
    test_client, _ = client
    response = test_client.get("/api/logs")
    assert response.status_code == 200
    data = response.json()
    assert data["log_files"] == []


def test_logs_list_nonexistent_dir(monkeypatch, tmp_path):
    """log_dir does not exist → 200, empty list."""
    nonexistent = tmp_path / "does_not_exist"
    monkeypatch.setattr("config.settings.settings.log_dir", nonexistent)
    app = create_app()
    test_client = TestClient(app, follow_redirects=False)
    response = test_client.get("/api/logs")
    assert response.status_code == 200
    data = response.json()
    assert data["log_files"] == []


def test_logs_list_shows_valid_files(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("line one\n")
    (log_dir / "10-04-2026.log").write_text("line two\n")
    response = test_client.get("/api/logs")
    assert response.status_code == 200
    data = response.json()
    filenames = [f["filename"] for f in data["log_files"]]
    assert "11-04-2026.log" in filenames
    assert "10-04-2026.log" in filenames


def test_logs_list_sorted_newest_first(client):
    test_client, log_dir = client
    (log_dir / "09-04-2026.log").write_text("old\n")
    (log_dir / "11-04-2026.log").write_text("newest\n")
    (log_dir / "10-04-2026.log").write_text("middle\n")
    response = test_client.get("/api/logs")
    data = response.json()
    filenames = [f["filename"] for f in data["log_files"]]
    assert filenames.index("11-04-2026.log") < filenames.index("10-04-2026.log")
    assert filenames.index("10-04-2026.log") < filenames.index("09-04-2026.log")


def test_logs_list_ignores_invalid_filenames(client):
    test_client, log_dir = client
    (log_dir / "debug.log").write_text("ignored\n")
    (log_dir / "not-a-log.txt").write_text("also ignored\n")
    (log_dir / "11-04-2026.log").write_text("valid\n")
    response = test_client.get("/api/logs")
    data = response.json()
    filenames = [f["filename"] for f in data["log_files"]]
    assert "debug.log" not in filenames
    assert "not-a-log.txt" not in filenames
    assert "11-04-2026.log" in filenames


# ---------------------------------------------------------------------------
# View endpoint
# ---------------------------------------------------------------------------


def test_log_view_renders_content(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("INFO something happened\nERROR oh no\n")
    response = test_client.get("/api/logs/11-04-2026.log")
    assert response.status_code == 200
    data = response.json()
    assert "INFO something happened" in data["lines"]
    assert "ERROR oh no" in data["lines"]


def test_log_view_shows_line_count(client):
    test_client, log_dir = client
    (log_dir / "11-04-2026.log").write_text("a\nb\nc\nd\ne\n")
    response = test_client.get("/api/logs/11-04-2026.log")
    data = response.json()
    assert data["line_count"] == 5


def test_log_view_not_found(client):
    test_client, _ = client
    response = test_client.get("/api/logs/11-04-2026.log")
    assert response.status_code == 404


def test_log_view_invalid_filename(client):
    test_client, _ = client
    response = test_client.get("/api/logs/debug.log")
    assert response.status_code == 404


def test_log_view_rejects_path_traversal(client):
    test_client, _ = client
    response = test_client.get("/api/logs/..passwd")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# _list_log_files helper — bad date values in otherwise valid-pattern filename
# ---------------------------------------------------------------------------


def test_list_log_files_handles_bad_date(tmp_path, monkeypatch):
    """A filename matching the pattern but with invalid date values is skipped."""
    monkeypatch.setattr("config.settings.settings.log_dir", tmp_path)
    (tmp_path / "01-13-2026.log").write_text("bad date\n")
    (tmp_path / "11-04-2026.log").write_text("good\n")

    from dashboard.routes_logs import _list_log_files

    result = _list_log_files()
    filenames = [f["filename"] for f in result]
    assert "01-13-2026.log" not in filenames
    assert "11-04-2026.log" in filenames
