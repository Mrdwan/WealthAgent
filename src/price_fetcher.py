"""Price fetcher — retrieves stock/commodity prices from Tiingo and yfinance.

Tiingo is the primary source for US equities (requires API key).
yfinance is the fallback for everything else and for commodities like silver.

All prices are converted to EUR using the FX rate from the same day
(or the most recent prior rate if the exact date is unavailable).

IWDA.L (London-listed iShares MSCI World ETF) is always included in the fetch
universe so that tracking-error calculations have a price to work from.
yfinance handles IWDA.L natively (Tiingo does not cover LSE); the existing
Tiingo-fails → yfinance-fallback path is sufficient — no special-case code
needed.  IWDA.L is quoted in USD on yfinance; the standard close_usd →
close_eur conversion therefore applies unchanged.

CLI usage (inside the container):
    python -m price_fetcher
"""

import logging
from datetime import date, timedelta

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

# ETF ticker always included so tracking-error calcs have a price
_IWDA_TICKER = "IWDA.L"

# Anomaly-detection thresholds (internal heuristics — not user-tunable)
_ANOMALY_THRESHOLD_PCT = 5.0
_CROSS_CHECK_TOLERANCE_PCT = 2.0


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
# Anomaly detection
# ---------------------------------------------------------------------------


def fetch_price_with_anomaly_check(
    ticker: str,
    prior_price_eur: float | None,
    current_eurusd: float,
) -> tuple[float | None, str]:
    """Fetch the Tiingo price for *ticker* and apply an anomaly cross-check.

    Compares the freshly fetched Tiingo price (converted to EUR via
    *current_eurusd*) to *prior_price_eur* (yesterday's stored EUR price).
    If the absolute percentage change exceeds ``_ANOMALY_THRESHOLD_PCT``:

    - Fetches the yfinance price as a cross-check.
    - If yfinance agrees within ``_CROSS_CHECK_TOLERANCE_PCT``, accepts Tiingo
      and logs an info message confirming the real move.
    - If yfinance disagrees, prefers yfinance and logs a warning.
    - If yfinance also fails, accepts Tiingo and logs a warning.

    When *prior_price_eur* is ``None`` (first-time ticker), the anomaly check
    is skipped entirely.

    Returns:
        ``(price_usd, source)`` — the chosen USD price and its source label
        (``'tiingo'`` or ``'yfinance'``).  Returns ``(None, '')`` if Tiingo
        fetch itself fails.
    """
    tiingo_price = fetch_tiingo_price(ticker)
    if tiingo_price is None:
        return None, ""

    # No prior price → skip anomaly check
    if prior_price_eur is None:
        return tiingo_price, "tiingo"

    tiingo_eur = tiingo_price / current_eurusd
    pct_change = abs((tiingo_eur - prior_price_eur) / prior_price_eur) * 100.0

    if pct_change <= _ANOMALY_THRESHOLD_PCT:
        return tiingo_price, "tiingo"

    # Change exceeds threshold — cross-check with yfinance
    yf_price = fetch_yfinance_price(ticker)

    if yf_price is None:
        log.warning(
            "Tiingo anomaly for %s: %.2f EUR (%.1f%% change) but yfinance unavailable;"
            " accepting Tiingo",
            ticker,
            tiingo_eur,
            pct_change,
        )
        return tiingo_price, "tiingo"

    yf_eur = yf_price / current_eurusd
    cross_diff = abs((tiingo_eur - yf_eur) / yf_eur) * 100.0

    if cross_diff <= _CROSS_CHECK_TOLERANCE_PCT:
        log.info(
            "Tiingo anomaly for %s confirmed as real move: %.1f%% change"
            " (Tiingo %.2f EUR vs yfinance %.2f EUR agree within %.1f%%)",
            ticker,
            pct_change,
            tiingo_eur,
            yf_eur,
            cross_diff,
        )
        return tiingo_price, "tiingo"

    log.warning(
        "Tiingo anomaly: %s Tiingo %.2f EUR vs yfinance %.2f EUR (diff %.1f%%); using yfinance",
        ticker,
        tiingo_eur,
        yf_eur,
        cross_diff,
    )
    return yf_price, "yfinance"


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


def _get_iwda_holdings_tickers() -> list[str]:
    """Return all distinct tickers from the most recent iwda_holdings snapshot.

    Returns an empty list if the table is empty.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return []
        latest_ts = row["fetched_at"]
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM iwda_holdings WHERE fetched_at = ?",
            (latest_ts,),
        ).fetchall()
    finally:
        conn.close()
    return [r["ticker"] for r in rows]


def _build_ticker_universe() -> list[str]:
    """Return the deduplicated, sorted union of all price-fetch targets.

    Combines:
    - Non-bond tickers from the ``holdings`` table.
    - All distinct tickers from the most recent ``iwda_holdings`` snapshot.
    - ``IWDA.L`` (always included for tracking-error calculations).
    """
    holdings = set(_get_holdings_tickers())
    iwda = set(_get_iwda_holdings_tickers())
    universe = holdings | iwda | {_IWDA_TICKER}
    return sorted(universe)


def fetch_all_prices() -> list[PricePoint]:
    """Fetch prices for every ticker in the universe, convert to EUR, and store.

    The universe is the union of non-bond holdings, the latest iwda_holdings
    snapshot, and IWDA.L (always).

    For each equity ticker fetched via Tiingo, an anomaly check compares the
    new price to yesterday's stored EUR price.  If the move exceeds
    ``_ANOMALY_THRESHOLD_PCT``, a yfinance cross-check is performed.

    Returns the list of PricePoint objects stored.
    """
    tickers = _build_ticker_universe()

    # Ensure we have fresh FX rates before converting
    try:
        fetch_ecb_rates()
    except Exception as exc:
        log.warning("Could not refresh ECB rates: %s — using cached rates", exc)

    today = date.today()
    today_str = today.isoformat()
    yesterday = today - timedelta(days=1)
    stored: list[PricePoint] = []

    # Look up today's EURUSD once (used for anomaly check conversions)
    try:
        current_eurusd = get_rate_for_date("EURUSD", today)
    except ValueError:
        current_eurusd = None

    for ticker in tickers:
        # --- anomaly check: look up yesterday's EUR price ---
        prior_pp = get_price_on_date(ticker, yesterday)
        prior_price_eur = prior_pp.close_eur if prior_pp is not None else None

        # Commodities bypass Tiingo entirely — skip anomaly check
        if ticker.upper() in _COMMODITY_MAP:
            price_usd, source = fetch_price(ticker)
        elif current_eurusd is not None:
            price_usd, source = fetch_price_with_anomaly_check(
                ticker, prior_price_eur, current_eurusd
            )
            # fetch_price_with_anomaly_check returns (None, '') on Tiingo failure
            # but we still want to try yfinance as a pure fallback
            if price_usd is None:
                log.info("Falling back to yfinance for %s", ticker)
                yf_price = fetch_yfinance_price(ticker)
                if yf_price is not None:
                    price_usd, source = yf_price, "yfinance"
        else:
            # No FX rate yet — use the simple fetch (anomaly check can't run)
            price_usd, source = fetch_price(ticker)

        if price_usd is None:
            log.error("Could not fetch price for %s — skipping", ticker)
            continue

        # Convert to EUR using same-day (or most-recent-prior) FX rate.
        # If no rate was available at the start of the loop, close_eur is None.
        if current_eurusd is not None:
            price_eur: float | None = price_usd / current_eurusd
        else:
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
