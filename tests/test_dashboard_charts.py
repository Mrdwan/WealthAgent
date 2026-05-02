"""Unit tests for src/dashboard/routes_charts.py (JSON API)."""

import pytest
from starlette.testclient import TestClient

from db import db_conn


@pytest.fixture()
def client():
    """TestClient for dashboard chart routes (no auth needed)."""
    from dashboard.app import create_app

    app = create_app()
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Portfolio value endpoint
# ---------------------------------------------------------------------------


def test_portfolio_value_empty(client):
    response = client.get("/api/charts/portfolio-value")
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == []
    assert data["datasets"][0]["data"] == []


def test_portfolio_value_with_data(client):
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )

    response = client.get("/api/charts/portfolio-value")
    assert response.status_code == 200
    data = response.json()
    assert len(data["labels"]) > 0
    assert data["datasets"][0]["data"][-1] == pytest.approx(1500.0)
    assert data["datasets"][0]["label"] == "Portfolio Value (EUR)"


def test_portfolio_value_skips_null_close_eur(client):
    """Dates with NULL close_eur are excluded from the chart."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('MSFT', 5, 200.0, 1.1, '2024-01-01', 'short_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('MSFT', ?, NULL)",
            (today,),
        )

    response = client.get("/api/charts/portfolio-value")
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == []
    assert data["datasets"][0]["data"] == []


def test_portfolio_value_partial_prices_excluded(client):
    """Dates where only some tickers have prices are excluded from the chart."""
    from datetime import date, timedelta

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('MSFT', 5, 200.0, 1.1, '2024-01-01', 'short_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 140.0)",
            (yesterday,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('MSFT', ?, 220.0)",
            (today,),
        )

    response = client.get("/api/charts/portfolio-value")
    assert response.status_code == 200
    data = response.json()
    assert today in data["labels"]
    assert yesterday not in data["labels"]


def test_portfolio_value_multiple_tickers(client):
    """Portfolio value sums across all tickers correctly."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('MSFT', 5, 200.0, 1.1, '2024-01-01', 'short_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('MSFT', ?, 220.0)",
            (today,),
        )

    response = client.get("/api/charts/portfolio-value")
    assert response.status_code == 200
    data = response.json()
    # 10*150 + 5*220 = 1500 + 1100 = 2600
    assert data["datasets"][0]["data"][-1] == pytest.approx(2600.0)


# ---------------------------------------------------------------------------
# P&L by ticker endpoint
# ---------------------------------------------------------------------------


def test_pnl_by_ticker_empty(client):
    response = client.get("/api/charts/pnl-by-ticker")
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == []
    assert data["datasets"][0]["data"] == []


def test_pnl_by_ticker_with_data(client):
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )

    response = client.get("/api/charts/pnl-by-ticker")
    assert response.status_code == 200
    data = response.json()
    assert "AAPL" in data["labels"]
    idx = data["labels"].index("AAPL")
    assert data["datasets"][0]["data"][idx] == pytest.approx(500.0)
    assert data["datasets"][0]["label"] == "Unrealized P&L (EUR)"


def test_pnl_by_ticker_positive_color(client):
    """Positive P&L uses green colour."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )

    response = client.get("/api/charts/pnl-by-ticker")
    data = response.json()
    idx = data["labels"].index("AAPL")
    assert data["datasets"][0]["backgroundColor"][idx] == "rgba(34, 197, 94, 0.7)"


def test_pnl_by_ticker_negative_color(client):
    """Negative P&L uses red colour."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 80.0)",
            (today,),
        )

    response = client.get("/api/charts/pnl-by-ticker")
    data = response.json()
    idx = data["labels"].index("AAPL")
    assert data["datasets"][0]["backgroundColor"][idx] == "rgba(239, 68, 68, 0.7)"


def test_pnl_by_ticker_no_price(client):
    """Holdings with no price entry are excluded from the result."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )

    response = client.get("/api/charts/pnl-by-ticker")
    data = response.json()
    assert data["labels"] == []


# ---------------------------------------------------------------------------
# Allocation endpoint
# ---------------------------------------------------------------------------


def test_allocation_empty(client):
    response = client.get("/api/charts/allocation")
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == []
    assert data["datasets"][0]["data"] == []


def test_allocation_with_data(client):
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('MSFT', 5, 200.0, 1.1, '2024-01-01', 'short_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('MSFT', ?, 220.0)",
            (today,),
        )

    response = client.get("/api/charts/allocation")
    assert response.status_code == 200
    data = response.json()
    assert "Long Term" in data["labels"]
    assert "Short Term" in data["labels"]
    lt_idx = data["labels"].index("Long Term")
    st_idx = data["labels"].index("Short Term")
    assert data["datasets"][0]["data"][lt_idx] == pytest.approx(1500.0)
    assert data["datasets"][0]["data"][st_idx] == pytest.approx(1100.0)


def test_allocation_display_names(client):
    """Pool names are converted to display names correctly."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('BND', 20, 50.0, 1.1, '2024-01-01', 'bond')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('BND', ?, 55.0)",
            (today,),
        )

    response = client.get("/api/charts/allocation")
    data = response.json()
    assert "Bond" in data["labels"]


def test_allocation_background_colors(client):
    """Background colours follow the spec for each pool."""
    from datetime import date

    today = date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate, "
            "purchase_date, pool) VALUES ('AAPL', 10, 100.0, 1.1, '2024-01-01', 'long_term')"
        )
        conn.execute(
            "INSERT INTO price_history (ticker, date, close_eur) VALUES ('AAPL', ?, 150.0)",
            (today,),
        )

    response = client.get("/api/charts/allocation")
    data = response.json()
    assert "#3b82f6" in data["datasets"][0]["backgroundColor"]


# ---------------------------------------------------------------------------
# Tax year endpoint
# ---------------------------------------------------------------------------


def test_tax_year_no_data(client, monkeypatch):
    """When no tax_year row exists, gains/exemption_used are zero; remaining = full exemption."""
    monkeypatch.setattr("config.settings.settings.annual_exemption", 1270.0)
    response = client.get("/api/charts/tax-year")
    assert response.status_code == 200
    data = response.json()
    assert len(data["labels"]) == 1
    ds_labels = [ds["label"] for ds in data["datasets"]]
    realized_idx = ds_labels.index("Realized Gains (EUR)")
    used_idx = ds_labels.index("Exemption Used (EUR)")
    remaining_idx = ds_labels.index("Exemption Remaining (EUR)")
    assert data["datasets"][realized_idx]["data"][0] == pytest.approx(0.0)
    assert data["datasets"][used_idx]["data"][0] == pytest.approx(0.0)
    assert data["datasets"][remaining_idx]["data"][0] == pytest.approx(1270.0)


def test_tax_year_with_data(client, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr("config.settings.settings.annual_exemption", 1270.0)
    current_year = datetime.now().year
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO tax_year (year, realized_gains_eur, exemption_used) VALUES (?, ?, ?)",
            (current_year, 1500.0, 750.0),
        )

    response = client.get("/api/charts/tax-year")
    assert response.status_code == 200
    data = response.json()
    assert f"Tax Year {current_year}" in data["labels"][0]

    ds_labels = [ds["label"] for ds in data["datasets"]]
    realized_idx = ds_labels.index("Realized Gains (EUR)")
    used_idx = ds_labels.index("Exemption Used (EUR)")
    remaining_idx = ds_labels.index("Exemption Remaining (EUR)")

    assert data["datasets"][realized_idx]["data"][0] == pytest.approx(1500.0)
    assert data["datasets"][used_idx]["data"][0] == pytest.approx(750.0)
    assert data["datasets"][remaining_idx]["data"][0] == pytest.approx(520.0)


def test_tax_year_remaining_floors_at_zero(client, monkeypatch):
    """Remaining exemption cannot go below zero."""
    from datetime import datetime

    monkeypatch.setattr("config.settings.settings.annual_exemption", 1270.0)
    current_year = datetime.now().year
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO tax_year (year, realized_gains_eur, exemption_used) VALUES (?, ?, ?)",
            (current_year, 3000.0, 2000.0),
        )

    response = client.get("/api/charts/tax-year")
    data = response.json()
    ds_labels = [ds["label"] for ds in data["datasets"]]
    remaining_idx = ds_labels.index("Exemption Remaining (EUR)")
    assert data["datasets"][remaining_idx]["data"][0] == pytest.approx(0.0)
