"""Price fetcher — retrieves stock/commodity prices from Tiingo and yfinance.

Tiingo is the primary source for US equities (requires API key).
yfinance is the fallback for everything else and for commodities like silver.

All prices are converted to EUR using the FX rate from the same day
(or the most recent prior rate if the exact date is unavailable).

CLI usage (inside the container):
    python -m price_fetcher
"""

import logging
from datetime import date

import requests
import yfinance as yf

from config.settings import settings
from db import PricePoint, db_conn, get_conn
from fx_fetcher import fetch_ecb_rates, get_rate_for_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_TIINGO_TIMEOUT = 15
_YFINANCE_TIMEOUT = 30

# Commodity ticker mapping — yfinance symbols
_COMMODITY_MAP: dict[str, str] = {
    "XAG": "SI=F",
    "XAGUSD": "SI=F",
    "SI": "SI=F",
}


# ---------------------------------------------------------------------------
# Tiingo (US equities)
# ---------------------------------------------------------------------------


def _tiingo_headers() -> dict[str, str]:
    """Return Tiingo auth headers.  Raises if API key is missing."""
    key = settings.tiingo_api_key
    if not key:
        raise OSError("TIINGO_API_KEY is not set")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Token {key}",
    }


def fetch_tiingo_price(ticker: str) -> float | None:
    """Fetch the latest price for *ticker* from Tiingo IEX endpoint.

    Returns the last price as a float, or ``None`` on failure.
    """
    url = f"https://api.tiingo.com/iex/{ticker}"
    try:
        resp = requests.get(
            url,
            headers=_tiingo_headers(),
            timeout=_TIINGO_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            log.warning("Tiingo returned empty data for %s", ticker)
            return None
        # IEX endpoint returns a list with one element
        entry = data[0] if isinstance(data, list) else data
        price = entry.get("last") or entry.get("tngoLast") or entry.get("close")
        if price is None:
            log.warning("Tiingo: no price field found for %s", ticker)
            return None
        return float(price)
    except Exception as exc:
        log.warning("Tiingo fetch failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# yfinance (universal fallback)
# ---------------------------------------------------------------------------


def fetch_yfinance_price(ticker: str) -> float | None:
    """Fetch the latest price for *ticker* via yfinance.

    Handles commodity ticker mapping (e.g. XAG -> XAGUSD=X).
    Returns the price as a float, or ``None`` on failure.
    """
    yf_ticker = _COMMODITY_MAP.get(ticker.upper(), ticker)
    try:
        tk = yf.Ticker(yf_ticker)
        # fast_info is the lightweight accessor
        info = tk.fast_info
        price = getattr(info, "last_price", None)
        if price is None:
            # Fallback to history
            hist = tk.history(period="1d")
            if hist.empty:
                log.warning("yfinance: no history for %s", yf_ticker)
                return None
            price = float(hist["Close"].iloc[-1])
        return float(price)
    except Exception as exc:
        log.warning("yfinance fetch failed for %s (%s): %s", ticker, yf_ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Combined fetch with fallback
# ---------------------------------------------------------------------------


def fetch_price(ticker: str) -> tuple[float | None, str]:
    """Fetch the current price for *ticker*, trying Tiingo first.

    Returns ``(price, source)`` where source is ``'tiingo'`` or ``'yfinance'``.
    If both fail, returns ``(None, '')``.
    """
    # Commodities go straight to yfinance
    if ticker.upper() in _COMMODITY_MAP:
        price = fetch_yfinance_price(ticker)
        return (price, "yfinance") if price else (None, "")

    # Try Tiingo first for equities
    price = fetch_tiingo_price(ticker)
    if price is not None:
        return price, "tiingo"

    log.info("Falling back to yfinance for %s", ticker)
    price = fetch_yfinance_price(ticker)
    if price is not None:
        return price, "yfinance"

    return None, ""


# ---------------------------------------------------------------------------
# Portfolio-wide fetch
# ---------------------------------------------------------------------------


def _get_holdings_tickers() -> list[str]:
    """Read all non-bond tickers from the holdings table."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM holdings WHERE pool != 'bond'").fetchall()
    finally:
        conn.close()
    return [row["ticker"] for row in rows]


def fetch_all_prices() -> list[PricePoint]:
    """Fetch prices for every non-bond holding, convert to EUR, and store.

    Returns the list of PricePoint objects stored.
    """
    tickers = _get_holdings_tickers()
    if not tickers:
        log.info("No non-bond holdings found — nothing to fetch.")
        return []

    # Ensure we have fresh FX rates before converting
    try:
        fetch_ecb_rates()
    except Exception as exc:
        log.warning("Could not refresh ECB rates: %s — using cached rates", exc)

    today = date.today()
    today_str = today.isoformat()
    stored: list[PricePoint] = []

    for ticker in tickers:
        price_usd, source = fetch_price(ticker)
        if price_usd is None:
            log.error("Could not fetch price for %s — skipping", ticker)
            continue

        # Convert to EUR using same-day (or most-recent-prior) FX rate
        try:
            eurusd = get_rate_for_date("EURUSD", today)
            price_eur = price_usd / eurusd
        except ValueError:
            log.warning(
                "No EURUSD rate available for %s — storing USD only for %s",
                today_str,
                ticker,
            )
            price_eur = None

        pp = PricePoint(
            ticker=ticker,
            date=today,
            close_usd=price_usd,
            close_eur=price_eur,
            source=source,
        )

        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO price_history"
                " (ticker, date, close_usd, close_eur, source)"
                " VALUES (?, ?, ?, ?, ?)",
                (pp.ticker, today_str, pp.close_usd, pp.close_eur, pp.source),
            )

        log.info(
            "%s: $%.2f (€%.2f) via %s",
            ticker,
            price_usd,
            price_eur if price_eur else 0,
            source,
        )
        stored.append(pp)

    return stored


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_current_price(ticker: str) -> PricePoint | None:
    """Return the most recent PricePoint for *ticker* from the DB."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM price_history WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return PricePoint(
        id=row["id"],
        ticker=row["ticker"],
        date=date.fromisoformat(row["date"]),
        close_usd=row["close_usd"],
        close_eur=row["close_eur"],
        source=row["source"],
    )


def get_price_on_date(ticker: str, target: str | date) -> PricePoint | None:
    """Return the PricePoint for *ticker* on or before *target*."""
    target_str = target.isoformat() if isinstance(target, date) else target
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM price_history WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (ticker, target_str),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return PricePoint(
        id=row["id"],
        ticker=row["ticker"],
        date=date.fromisoformat(row["date"]),
        close_usd=row["close_usd"],
        close_eur=row["close_eur"],
        source=row["source"],
    )


def get_price_change(ticker: str, days: int) -> float | None:
    """Return the percentage price change for *ticker* over *days* days.

    Uses EUR prices.  Returns ``None`` if insufficient data.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT close_eur, date FROM price_history"
            " WHERE ticker = ? AND close_eur IS NOT NULL"
            " ORDER BY date DESC LIMIT ?",
            (ticker, days + 1),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return None

    latest = rows[0]["close_eur"]
    oldest = rows[-1]["close_eur"]
    if oldest == 0:
        return None
    return ((latest - oldest) / oldest) * 100.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Fetch all portfolio prices and print a summary."""
    points = fetch_all_prices()
    if not points:
        print("No prices fetched (no non-bond holdings?).")
        return

    print(f"\nPrices as of {date.today().isoformat()}")
    print(f"{'Ticker':<10} {'USD':>10} {'EUR':>10} {'Source':<10}")
    print("-" * 42)
    for pp in sorted(points, key=lambda p: p.ticker):
        usd_str = f"${pp.close_usd:.2f}" if pp.close_usd else "—"
        eur_str = f"€{pp.close_eur:.2f}" if pp.close_eur else "—"
        print(f"{pp.ticker:<10} {usd_str:>10} {eur_str:>10} {pp.source:<10}")


if __name__ == "__main__":
    main()
