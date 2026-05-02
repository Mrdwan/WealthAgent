"""IWDA holdings and tracking-error API routes for the WealthAgent dashboard."""

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config.settings import settings
from db import get_conn

router = APIRouter(prefix="/api")


@router.get("/iwda")
async def iwda_holdings() -> JSONResponse:
    """Return the most recent IWDA top-N snapshot from the database."""
    top_n = settings.iwda_top_n
    conn = get_conn()
    try:
        # Latest fetched_at timestamp
        ts_row = conn.execute(
            "SELECT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()

        if ts_row is None:
            return JSONResponse({"holdings": [], "fetched_at": None, "top_n": top_n})

        fetched_at = ts_row["fetched_at"]
        rows = conn.execute(
            "SELECT ticker, name, weight_pct, rank FROM iwda_holdings"
            " WHERE fetched_at = ? AND rank <= ? ORDER BY rank",
            (fetched_at, top_n),
        ).fetchall()
    finally:
        conn.close()

    holdings = [
        {
            "ticker": r["ticker"],
            "name": r["name"],
            "weight_pct": r["weight_pct"],
            "rank": r["rank"],
        }
        for r in rows
    ]

    return JSONResponse({"holdings": holdings, "fetched_at": fetched_at, "top_n": top_n})


@router.get("/iwda/history")
async def iwda_history() -> JSONResponse:
    """Return all distinct IWDA snapshots with their timestamps."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 12"
        ).fetchall()
    finally:
        conn.close()

    snapshots = [r["fetched_at"] for r in rows]
    return JSONResponse({"snapshots": snapshots})


@router.get("/tracking-error")
async def tracking_error() -> JSONResponse:
    """Return 30-day tracking error: portfolio stocks-only return vs IWDA.L return."""
    conn = get_conn()
    try:
        # Get all held tickers and shares
        held = conn.execute(
            "SELECT ticker, SUM(shares) AS total_shares FROM holdings GROUP BY ticker"
        ).fetchall()

        if not held:
            return JSONResponse(
                {
                    "portfolio_return_pct": None,
                    "iwda_return_pct": None,
                    "tracking_error_pp": None,
                    "explanation": "No holdings in portfolio.",
                }
            )

        holdings_map: dict[str, float] = {r["ticker"]: r["total_shares"] for r in held}

        # Get latest and 30-days-ago prices
        def _portfolio_value(date_expr: str) -> float | None:
            total = 0.0
            for ticker, shares in holdings_map.items():
                row = conn.execute(
                    f"SELECT close_eur FROM price_history WHERE ticker = ? AND date <= {date_expr}"  # noqa: S608
                    " ORDER BY date DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                if row is None or row["close_eur"] is None:
                    return None
                total += shares * row["close_eur"]
            return total

        val_now = _portfolio_value("date('now')")
        val_30d = _portfolio_value("date('now', '-30 days')")

        # IWDA.L prices
        iwda_now_row = conn.execute(
            "SELECT close_eur FROM price_history WHERE ticker = 'IWDA.L'"
            " AND date <= date('now') ORDER BY date DESC LIMIT 1"
        ).fetchone()
        iwda_30d_row = conn.execute(
            "SELECT close_eur FROM price_history WHERE ticker = 'IWDA.L'"
            " AND date <= date('now', '-30 days') ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    port_return: float | None = None
    if val_now is not None and val_30d is not None and val_30d > 0:
        port_return = round((val_now - val_30d) / val_30d * 100, 2)

    iwda_return: float | None = None
    if iwda_now_row and iwda_30d_row:
        now_p = iwda_now_row["close_eur"]
        ago_p = iwda_30d_row["close_eur"]
        if now_p is not None and ago_p is not None and ago_p > 0:
            iwda_return = round((now_p - ago_p) / ago_p * 100, 2)

    tracking_pp: float | None = None
    explanation = "Insufficient price data."
    if port_return is not None and iwda_return is not None:
        tracking_pp = round(port_return - iwda_return, 2)
        if abs(tracking_pp) < 1.0:
            explanation = "Portfolio is tracking IWDA closely."
        elif tracking_pp > 0:
            explanation = f"Portfolio outperforming IWDA by {tracking_pp:.1f}pp."
        else:
            explanation = f"Portfolio underperforming IWDA by {abs(tracking_pp):.1f}pp."

    return JSONResponse(
        {
            "portfolio_return_pct": port_return,
            "iwda_return_pct": iwda_return,
            "tracking_error_pp": tracking_pp,
            "explanation": explanation,
        }
    )


@router.get("/holdings")
async def portfolio_holdings() -> JSONResponse:
    """Return all portfolio holdings with current prices and P&L."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.ticker,
                h.pool,
                SUM(h.shares) AS total_shares,
                SUM(h.shares * h.entry_price_eur) / SUM(h.shares) AS avg_cost_eur,
                h.purchase_date,
                p.close_eur AS current_price_eur
            FROM holdings h
            LEFT JOIN price_history p ON p.ticker = h.ticker
                AND p.date = (SELECT MAX(p2.date) FROM price_history p2 WHERE p2.ticker = h.ticker)
            GROUP BY h.ticker
            """
        ).fetchall()

        # Also get current tax year
        tax_row = conn.execute(
            "SELECT realized_gains_eur, exemption_used FROM tax_year WHERE year = ?",
            (datetime.now().year,),
        ).fetchone()
    finally:
        conn.close()

    holdings = []
    total_value = 0.0
    for r in rows:
        current = r["current_price_eur"]
        avg_cost = r["avg_cost_eur"]
        shares = r["total_shares"]
        value = (current or 0.0) * shares
        pnl = (current - avg_cost) * shares if current else None
        pnl_pct = ((current - avg_cost) / avg_cost * 100) if current and avg_cost else None
        total_value += value
        holdings.append(
            {
                "ticker": r["ticker"],
                "pool": r["pool"],
                "shares": shares,
                "avg_cost_eur": round(avg_cost, 2) if avg_cost else None,
                "current_price_eur": round(current, 2) if current else None,
                "value_eur": round(value, 2),
                "pnl_eur": round(pnl, 2) if pnl is not None else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            }
        )

    tax = {
        "realized_gains_eur": 0.0,
        "exemption_used": 0.0,
        "exemption_remaining": settings.annual_exemption,
    }
    if tax_row:
        gains = tax_row["realized_gains_eur"] or 0.0
        used = tax_row["exemption_used"] or 0.0
        tax = {
            "realized_gains_eur": gains,
            "exemption_used": used,
            "exemption_remaining": max(0.0, settings.annual_exemption - used),
        }

    return JSONResponse(
        {
            "holdings": holdings,
            "total_value_eur": round(total_value, 2),
            "tax_year": tax,
        }
    )
