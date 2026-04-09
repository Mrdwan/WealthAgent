"""Fundamentals fetcher — pulls key financial metrics via yfinance.

Stores data in the ``fundamentals`` table, including the raw yfinance
info dict as JSON for future use.

CLI usage (inside the container):
    python -m fundamentals
"""

import json
import logging
from datetime import date, datetime

import yfinance as yf

from db import Fundamentals, db_conn, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Tickers that are commodities / non-equity — skip fundamentals
_SKIP_TICKERS: set[str] = {"XAG", "XAGUSD", "SI"}


# ---------------------------------------------------------------------------
# Fetch & store
# ---------------------------------------------------------------------------


def fetch_fundamentals(ticker: str) -> Fundamentals | None:
    """Fetch fundamental data for *ticker* from yfinance and store it.

    Returns the Fundamentals model, or ``None`` if yfinance fails.
    """
    log.info("Fetching fundamentals for %s …", ticker)
    try:
        tk = yf.Ticker(ticker)
        info: dict = tk.info or {}
    except Exception as exc:
        log.warning("yfinance info failed for %s: %s", ticker, exc)
        return None

    if not info or info.get("quoteType") == "NONE_EQUITY":
        log.warning("No fundamentals data for %s", ticker)
        return None

    # Parse next earnings date from calendar
    next_earnings: date | None = None
    try:
        cal = tk.calendar
        if cal is not None:
            # cal can be a dict or DataFrame depending on yfinance version
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if earnings_dates:
                    ed = earnings_dates[0]
                    if hasattr(ed, "date"):
                        next_earnings = ed.date()
                    elif isinstance(ed, str):
                        next_earnings = date.fromisoformat(ed[:10])
    except Exception as exc:
        log.debug("Could not parse earnings date for %s: %s", ticker, exc)

    now = datetime.now()
    fund = Fundamentals(
        ticker=ticker,
        fetched_at=now,
        pe_ratio=info.get("trailingPE") or info.get("forwardPE"),
        ps_ratio=info.get("priceToSalesTrailing12Months"),
        revenue_growth=info.get("revenueGrowth"),
        profit_margin=info.get("profitMargins"),
        free_cash_flow=info.get("freeCashflow"),
        debt_to_equity=info.get("debtToEquity"),
        dividend_yield=info.get("dividendYield"),
        market_cap=info.get("marketCap"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        country=info.get("country"),
        next_earnings=next_earnings,
        raw_json=json.dumps(info, default=str),
    )

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fundamentals"
            " (ticker, fetched_at, pe_ratio, ps_ratio, revenue_growth,"
            "  profit_margin, free_cash_flow, debt_to_equity, dividend_yield,"
            "  market_cap, sector, industry, country, next_earnings, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fund.ticker,
                fund.fetched_at.isoformat(),
                fund.pe_ratio,
                fund.ps_ratio,
                fund.revenue_growth,
                fund.profit_margin,
                fund.free_cash_flow,
                fund.debt_to_equity,
                fund.dividend_yield,
                fund.market_cap,
                fund.sector,
                fund.industry,
                fund.country,
                fund.next_earnings.isoformat() if fund.next_earnings else None,
                fund.raw_json,
            ),
        )

    log.info(
        "%s: P/E=%.1f  sector=%s  cap=%s",
        ticker,
        fund.pe_ratio or 0,
        fund.sector or "?",
        _fmt_cap(fund.market_cap),
    )
    return fund


def _fmt_cap(cap: float | None) -> str:
    """Format market cap for display."""
    if cap is None:
        return "—"
    if cap >= 1e12:
        return f"${cap / 1e12:.1f}T"
    if cap >= 1e9:
        return f"${cap / 1e9:.1f}B"
    if cap >= 1e6:
        return f"${cap / 1e6:.0f}M"
    return f"${cap:,.0f}"


def _get_stock_tickers() -> list[str]:
    """Read all non-bond, non-commodity tickers from holdings."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM holdings WHERE pool != 'bond'"
        ).fetchall()
    finally:
        conn.close()
    return [
        row["ticker"]
        for row in rows
        if row["ticker"].upper() not in _SKIP_TICKERS
    ]


def fetch_all_fundamentals() -> list[Fundamentals]:
    """Fetch fundamentals for every stock holding.

    Skips bonds and commodities. Continues on failure for individual tickers.
    """
    tickers = _get_stock_tickers()
    if not tickers:
        log.info("No stock holdings found — nothing to fetch.")
        return []

    results: list[Fundamentals] = []
    for ticker in tickers:
        fund = fetch_fundamentals(ticker)
        if fund is not None:
            results.append(fund)

    log.info("Fetched fundamentals for %d / %d tickers", len(results), len(tickers))
    return results


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_latest_fundamentals(ticker: str) -> Fundamentals | None:
    """Return the most recent Fundamentals snapshot for *ticker*."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fundamentals WHERE ticker = ?"
            " ORDER BY fetched_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return Fundamentals(
        id=row["id"],
        ticker=row["ticker"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        pe_ratio=row["pe_ratio"],
        ps_ratio=row["ps_ratio"],
        revenue_growth=row["revenue_growth"],
        profit_margin=row["profit_margin"],
        free_cash_flow=row["free_cash_flow"],
        debt_to_equity=row["debt_to_equity"],
        dividend_yield=row["dividend_yield"],
        market_cap=row["market_cap"],
        sector=row["sector"],
        industry=row["industry"],
        country=row["country"],
        next_earnings=(
            date.fromisoformat(row["next_earnings"])
            if row["next_earnings"]
            else None
        ),
        raw_json=row["raw_json"],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Fetch all fundamentals and print a summary table."""
    results = fetch_all_fundamentals()
    if not results:
        print("No fundamentals fetched (no stock holdings?).")
        return

    print(f"\nFundamentals as of {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(
        f"{'Ticker':<8} {'P/E':>7} {'P/S':>7} {'Rev Gr':>7} "
        f"{'Margin':>7} {'D/E':>7} {'Cap':>10} {'Sector':<20}"
    )
    print("-" * 80)
    for f in sorted(results, key=lambda x: x.ticker):
        pe = f"{f.pe_ratio:.1f}" if f.pe_ratio else "—"
        ps = f"{f.ps_ratio:.1f}" if f.ps_ratio else "—"
        rg = f"{f.revenue_growth * 100:.0f}%" if f.revenue_growth else "—"
        pm = f"{f.profit_margin * 100:.0f}%" if f.profit_margin else "—"
        de = f"{f.debt_to_equity:.0f}" if f.debt_to_equity else "—"
        cap = _fmt_cap(f.market_cap)
        sector = (f.sector or "—")[:20]
        print(
            f"{f.ticker:<8} {pe:>7} {ps:>7} {rg:>7} "
            f"{pm:>7} {de:>7} {cap:>10} {sector:<20}"
        )


if __name__ == "__main__":
    main()
