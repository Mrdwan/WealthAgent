"""Chart data API routes for the WealthAgent dashboard."""

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config.settings import settings
from db import get_conn

router = APIRouter()

_POOL_DISPLAY_NAMES: dict[str, str] = {
    "long_term": "Long Term",
    "short_term": "Short Term",
    "bond": "Bond",
}

_POOL_COLORS: dict[str, str] = {
    "long_term": "#3b82f6",
    "short_term": "#f59e0b",
    "bond": "#10b981",
}

_COLOR_POSITIVE = "rgba(34, 197, 94, 0.7)"
_COLOR_NEGATIVE = "rgba(239, 68, 68, 0.7)"


@router.get("/api/charts/portfolio-value")
async def portfolio_value_data() -> JSONResponse:
    """Return portfolio value over time as Chart.js line data."""
    conn = get_conn()
    try:
        holding_rows = conn.execute(
            "SELECT ticker, SUM(shares) AS total_shares FROM holdings GROUP BY ticker"
        ).fetchall()

        price_rows = conn.execute(
            """
            SELECT ticker, date, close_eur FROM price_history
            WHERE date >= date('now', '-90 days') AND close_eur IS NOT NULL
            ORDER BY date
            """
        ).fetchall()
    finally:
        conn.close()

    holdings: dict[str, float] = {row["ticker"]: row["total_shares"] for row in holding_rows}

    # Build {date: {ticker: price}}
    prices_by_date: dict[str, dict[str, float]] = {}
    for row in price_rows:
        d = row["date"]
        if d not in prices_by_date:
            prices_by_date[d] = {}
        prices_by_date[d][row["ticker"]] = row["close_eur"]

    # Compute daily portfolio value — only include dates where ALL held tickers have a price
    dates: list[str] = []
    values: list[float] = []
    for d in sorted(prices_by_date):
        day_prices = prices_by_date[d]
        total = 0.0
        for ticker, shares in holdings.items():
            price = day_prices.get(ticker)
            if price is None:
                break
            total += shares * price
        else:
            dates.append(d)
            values.append(round(total, 2))

    return JSONResponse(
        {
            "labels": dates,
            "datasets": [{"label": "Portfolio Value (EUR)", "data": values}],
        }
    )


@router.get("/api/charts/pnl-by-ticker")
async def pnl_by_ticker_data() -> JSONResponse:
    """Return unrealized P&L per ticker as Chart.js bar data."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.ticker,
                SUM(h.shares) AS total_shares,
                SUM(h.shares * h.entry_price_eur) / SUM(h.shares) AS avg_cost,
                p.close_eur AS current_price
            FROM holdings h
            LEFT JOIN price_history p ON p.ticker = h.ticker
                AND p.date = (SELECT MAX(p2.date) FROM price_history p2 WHERE p2.ticker = h.ticker)
            GROUP BY h.ticker
            """
        ).fetchall()
    finally:
        conn.close()

    items: list[tuple[str, float]] = []
    for row in rows:
        if row["current_price"] is None:
            continue
        pnl = (row["current_price"] - row["avg_cost"]) * row["total_shares"]
        items.append((row["ticker"], round(pnl, 2)))

    # Sort descending by P&L
    items.sort(key=lambda x: x[1], reverse=True)

    labels = [t for t, _ in items]
    data = [v for _, v in items]
    colors = [_COLOR_POSITIVE if v >= 0 else _COLOR_NEGATIVE for v in data]

    return JSONResponse(
        {
            "labels": labels,
            "datasets": [
                {
                    "label": "Unrealized P&L (EUR)",
                    "data": data,
                    "backgroundColor": colors,
                }
            ],
        }
    )


@router.get("/api/charts/allocation")
async def allocation_data() -> JSONResponse:
    """Return allocation by pool as Chart.js pie data."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.pool,
                SUM(h.shares * p.close_eur) AS pool_value
            FROM holdings h
            LEFT JOIN price_history p ON p.ticker = h.ticker
                AND p.date = (SELECT MAX(p2.date) FROM price_history p2 WHERE p2.ticker = h.ticker)
            WHERE p.close_eur IS NOT NULL
            GROUP BY h.pool
            """
        ).fetchall()
    finally:
        conn.close()

    labels: list[str] = []
    data: list[float] = []
    colors: list[str] = []

    for row in rows:
        pool = row["pool"]
        labels.append(_POOL_DISPLAY_NAMES.get(pool, pool))
        data.append(round(row["pool_value"], 2))
        colors.append(_POOL_COLORS.get(pool, "#6b7280"))

    return JSONResponse(
        {
            "labels": labels,
            "datasets": [{"data": data, "backgroundColor": colors}],
        }
    )


@router.get("/api/charts/tax-year")
async def tax_year_data() -> JSONResponse:
    """Return tax year gains vs exemption as Chart.js bar data."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT realized_gains_eur, exemption_used FROM tax_year"
            " WHERE year = strftime('%Y', 'now')"
        ).fetchone()
    finally:
        conn.close()

    current_year = datetime.now().year

    if row is None:
        realized_gains: float = 0.0
        exemption_used: float = 0.0
    else:
        realized_gains = row["realized_gains_eur"] or 0.0
        exemption_used = row["exemption_used"] or 0.0

    remaining = max(0.0, settings.annual_exemption - exemption_used)

    return JSONResponse(
        {
            "labels": [f"Tax Year {current_year}"],
            "datasets": [
                {
                    "label": "Realized Gains (EUR)",
                    "data": [round(realized_gains, 2)],
                    "backgroundColor": "#f59e0b",
                },
                {
                    "label": "Exemption Used (EUR)",
                    "data": [round(exemption_used, 2)],
                    "backgroundColor": "#ef4444",
                },
                {
                    "label": "Exemption Remaining (EUR)",
                    "data": [round(remaining, 2)],
                    "backgroundColor": "#10b981",
                },
            ],
        }
    )
