"""Unit tests for src/dashboard/routes_reports.py."""

import pytest
from starlette.testclient import TestClient

from dashboard.app import create_app
from dashboard.auth import create_session_token


@pytest.fixture()
def client(monkeypatch):
    """Authenticated TestClient for dashboard report routes."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    app = create_app()
    test_client = TestClient(app, follow_redirects=False)
    token = create_session_token()
    test_client.cookies.set("wa_session", token)
    return test_client


@pytest.fixture()
def unauth_client(monkeypatch):
    """Unauthenticated TestClient."""
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "testpassword")
    app = create_app()
    return TestClient(app, follow_redirects=False)


def _insert_report(report_type: str = "rebalance", ticker: str | None = None) -> int:
    """Insert a report directly via save_report."""
    from reports import save_report

    return save_report(
        report_type, f"Full content for {report_type}.", ticker=ticker, summary="Test summary."
    )


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_reports_list_requires_auth(unauth_client):
    response = unauth_client.get("/reports")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_report_detail_requires_auth(unauth_client):
    response = unauth_client.get("/reports/1")
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------


def test_reports_list_empty(client):
    response = client.get("/reports")
    assert response.status_code == 200


def test_reports_list_shows_reports(client):
    _insert_report("rebalance")
    _insert_report("analyze", ticker="AAPL")
    response = client.get("/reports")
    assert response.status_code == 200
    assert "rebalance" in response.text
    assert "analyze" in response.text
    assert "AAPL" in response.text


def test_reports_list_pagination(client):
    # Insert 25 reports so we get 2 pages
    from reports import save_report

    for i in range(25):
        save_report("rebalance", f"Content {i}.", summary=f"Summary {i}.")

    response_p1 = client.get("/reports?page=1")
    assert response_p1.status_code == 200
    assert "Page 1 of 2" in response_p1.text

    response_p2 = client.get("/reports?page=2")
    assert response_p2.status_code == 200
    assert "Page 2 of 2" in response_p2.text


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def test_report_detail_renders(client):
    report_id = _insert_report("analyze", ticker="MSFT")
    response = client.get(f"/reports/{report_id}")
    assert response.status_code == 200
    assert "analyze" in response.text
    assert "MSFT" in response.text


def test_report_detail_not_found(client):
    response = client.get("/reports/9999")
    assert response.status_code == 404
