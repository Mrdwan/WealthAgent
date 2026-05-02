"""Unit tests for src/dashboard/routes_iwda.py (JSON API)."""

from datetime import date, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client():
    """TestClient for IWDA dashboard routes (no auth needed)."""
    from dashboard.app import create_app

    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /api/iwda — IWDA holdings
# ---------------------------------------------------------------------------


def test_iwda_holdings_empty(client):
    """No IWDA holdings → returns empty list."""
    response = client.get("/api/iwda")
    assert response.status_code == 200
    data = response.json()
    assert data["holdings"] == []
    assert data["fetched_at"] is None


def test_iwda_holdings_with_data(client, monkeypatch):
    """Returns top-N IWDA holdings from latest snapshot."""
    monkeypatch.setattr("config.settings.settings.iwda_top_n", 3)
    now = datetime.now().isoformat()
    with db_conn() as conn:
        for i, (ticker, weight) in enumerate(
            [("AAPL", 5.0), ("MSFT", 4.0), ("NVDA", 3.5), ("AMZN", 2.0)], start=1
        ):
            conn.execute(
                "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (ticker, f"{ticker} Inc", weight, i, now),
            )

    response = client.get("/api/iwda")
    assert response.status_code == 200
    data = response.json()
    assert len(data["holdings"]) == 3
    assert data["holdings"][0]["ticker"] == "AAPL"
    assert data["top_n"] == 3
    assert data["fetched_at"] == now


# ---------------------------------------------------------------------------
# GET /api/iwda/history — IWDA snapshot timestamps
# ---------------------------------------------------------------------------


def test_iwda_history_empty(client):
    """No snapshots → returns empty list."""
    response = client.get("/api/iwda/history")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshots"] == []


def test_iwda_history_with_data(client):
    """Returns distinct timestamps ordered newest first."""
    ts1 = "2026-04-01T00:00:00"
    ts2 = "2026-05-01T00:00:00"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES ('AAPL', 'Apple', 5.0, 1, ?)",
            (ts1,),
        )
        conn.execute(
            "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES ('AAPL', 'Apple', 5.1, 1, ?)",
            (ts2,),
        )

    response = client.get("/api/iwda/history")
    data = response.json()
    assert len(data["snapshots"]) == 2
    assert data["snapshots"][0] == ts2  # newest first


# ---------------------------------------------------------------------------
# GET /api/tracking-error
# ---------------------------------------------------------------------------


def test_tracking_error_no_holdings(client):
    """No holdings → returns null values with explanation."""
    response = client.get("/api/tracking-error")
    assert response.status_code == 200
    data = response.json()
    assert data["portfolio_return_pct"] is None
    assert data["tracking_error_pp"] is None
    assert "No holdings" in data["explanation"]


def test_tracking_error_insufficient_data(client):
    """Holdings but no prices → returns null tracking error."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert data["portfolio_return_pct"] is None
    assert "Insufficient" in data["explanation"]


def test_tracking_error_with_data(client):
    """Full price data → returns calculated tracking error."""
    today = date.today().isoformat()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        # Portfolio prices: 30 days ago = 100, today = 110 → +10%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 110.0, 'tiingo')",
            (today,),
        )
        # IWDA.L prices: 30 days ago = 80, today = 84 → +5%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 80.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 84.0, 'tiingo')",
            (today,),
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert data["portfolio_return_pct"] == pytest.approx(10.0)
    assert data["iwda_return_pct"] == pytest.approx(5.0)
    assert data["tracking_error_pp"] == pytest.approx(5.0)
    assert "outperforming" in data["explanation"]


def test_tracking_error_zero_past_price(client):
    """Past price is 0, skipping division by zero."""
    today = date.today().isoformat()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 0.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 105.0, 'tiingo')",
            (today,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 0.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 110.0, 'tiingo')",
            (today,),
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert data["iwda_return_pct"] is None
    assert data["portfolio_return_pct"] is None


def test_tracking_error_underperforming(client):
    """Portfolio underperforms IWDA."""
    today = date.today().isoformat()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        # Portfolio +5%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 105.0, 'tiingo')",
            (today,),
        )
        # IWDA +10%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 110.0, 'tiingo')",
            (today,),
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert data["tracking_error_pp"] == pytest.approx(-5.0)
    assert "underperforming" in data["explanation"]


def test_tracking_error_closely_tracking(client):
    """Portfolio tracks IWDA closely (< 1.0pp diff)."""
    today = date.today().isoformat()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        # Portfolio +5%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 105.0, 'tiingo')",
            (today,),
        )
        # IWDA +5.5%
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, 105.5, 'tiingo')",
            (today,),
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert "tracking IWDA closely" in data["explanation"]


def test_tracking_error_iwda_missing_price(client):
    """IWDA price is NULL, resulting in missing return."""
    today = date.today().isoformat()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 100.0, 'tiingo')",
            (thirty_ago,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 105.0, 'tiingo')",
            (today,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('IWDA.L', ?, NULL, 'tiingo')",
            (today,),
        )

    response = client.get("/api/tracking-error")
    data = response.json()
    assert data["iwda_return_pct"] is None


# ---------------------------------------------------------------------------
# GET /api/holdings — portfolio holdings
# ---------------------------------------------------------------------------


def test_portfolio_holdings_empty(client):
    """No holdings → empty list."""
    response = client.get("/api/holdings")
    assert response.status_code == 200
    data = response.json()
    assert data["holdings"] == []
    assert data["total_value_eur"] == 0.0


def test_portfolio_holdings_with_data(client):
    """Holdings with prices → returns calculated P&L."""
    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,"
            " purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur, source)"
            " VALUES ('AAPL', ?, 150.0, 'tiingo')",
            (today,),
        )

    response = client.get("/api/holdings")
    data = response.json()
    assert len(data["holdings"]) == 1
    h = data["holdings"][0]
    assert h["ticker"] == "AAPL"
    assert h["shares"] == 10.0
    assert h["avg_cost_eur"] == pytest.approx(100.0)
    assert h["current_price_eur"] == pytest.approx(150.0)
    assert h["value_eur"] == pytest.approx(1500.0)
    assert h["pnl_eur"] == pytest.approx(500.0)
    assert h["pnl_pct"] == pytest.approx(50.0)
    assert data["total_value_eur"] == pytest.approx(1500.0)


def test_portfolio_holdings_includes_tax(client):
    """Tax year data is included in the response."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO tax_year (year, realized_gains_eur, exemption_used) VALUES (?, ?, ?)",
            (datetime.now().year, 500.0, 200.0),
        )

    response = client.get("/api/holdings")
    data = response.json()
    assert data["tax_year"]["realized_gains_eur"] == pytest.approx(500.0)
    assert data["tax_year"]["exemption_used"] == pytest.approx(200.0)
    assert data["tax_year"]["exemption_remaining"] == pytest.approx(1070.0)
