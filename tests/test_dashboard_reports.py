"""Unit tests for src/dashboard/routes_reports.py (JSON API)."""

import pytest
from starlette.testclient import TestClient

from dashboard.app import create_app


@pytest.fixture()
def client():
    """TestClient for dashboard report routes (no auth needed)."""
    app = create_app()
    return TestClient(app, follow_redirects=False)


def _insert_report(report_type: str = "rebalance", ticker: str | None = None) -> int:
    """Insert a report directly via save_report."""
    from reports import save_report

    return save_report(
        report_type, f"Full content for {report_type}.", ticker=ticker, summary="Test summary."
    )


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_reports_list_empty(client):
    response = client.get("/api/reports")
    assert response.status_code == 200
    data = response.json()
    assert data["reports"] == []
    assert data["total"] == 0


def test_reports_list_shows_reports(client):
    _insert_report("rebalance")
    _insert_report("analyze", ticker="AAPL")
    response = client.get("/api/reports")
    assert response.status_code == 200
    data = response.json()
    assert len(data["reports"]) == 2
    tickers = [r["ticker"] for r in data["reports"]]
    assert "AAPL" in tickers
    types = [r["report_type"] for r in data["reports"]]
    assert "rebalance" in types
    assert "analyze" in types


def test_reports_list_pagination(client):
    from reports import save_report

    for i in range(25):
        save_report("rebalance", f"Content {i}.", summary=f"Summary {i}.")

    response_p1 = client.get("/api/reports?page=1")
    data_p1 = response_p1.json()
    assert data_p1["page"] == 1
    assert data_p1["total_pages"] == 2
    assert len(data_p1["reports"]) == 20

    response_p2 = client.get("/api/reports?page=2")
    data_p2 = response_p2.json()
    assert data_p2["page"] == 2
    assert len(data_p2["reports"]) == 5


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


def test_report_detail_renders(client):
    report_id = _insert_report("analyze", ticker="MSFT")
    response = client.get(f"/api/reports/{report_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["report_type"] == "analyze"
    assert data["ticker"] == "MSFT"
    assert "Full content" in data["full_content"]


def test_report_detail_not_found(client):
    response = client.get("/api/reports/9999")
    assert response.status_code == 404
