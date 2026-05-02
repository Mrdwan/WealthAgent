"""Integration tests for data fetcher modules.

These hit real APIs — run manually, not in CI.

Usage (inside the container):
    python -m pytest tests/test_fetchers.py -v
    # or without pytest:
    python tests/test_fetchers.py
"""

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — point DB at a temp file so tests don't touch production data
# ---------------------------------------------------------------------------
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp_db.name

# In the container, src/ files live at /app/. Locally, they're under ../src/.
_app_dir = Path("/app")
_src_dir = Path(__file__).resolve().parent.parent / "src"
for d in (_app_dir, _src_dir):
    if d.exists() and str(d) not in sys.path:
        sys.path.insert(0, str(d))

from db import init_db  # noqa: E402

init_db()

# ---------------------------------------------------------------------------
# Now import the fetchers (after DB is ready)
# ---------------------------------------------------------------------------

from fx_fetcher import (  # noqa: E402
    fetch_ecb_rates,
    get_latest_rate,
    get_rate_for_date,
    usd_to_eur,
)
from price_fetcher import (  # noqa: E402
    fetch_price,
    fetch_tiingo_price,
    fetch_yfinance_price,
    get_current_price,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record a pass/fail result."""
    global _passed, _failed
    status = "PASS" if condition else "FAIL"
    if not condition:
        _failed += 1
    else:
        _passed += 1
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


# ---------------------------------------------------------------------------
# FX Fetcher tests
# ---------------------------------------------------------------------------


def test_fx_fetcher() -> None:
    """Test ECB rate fetching and query helpers."""
    print("\n=== FX Fetcher ===")

    # Fetch rates
    rates = fetch_ecb_rates()
    check("ECB returns rates", len(rates) > 0, f"{len(rates)} pairs")
    check("ECB rates include USD", any(r.pair == "EURUSD" for r in rates))
    check("ECB rates include GBP", any(r.pair == "EURGBP" for r in rates))

    # Sanity-check EURUSD range
    eurusd = get_latest_rate("EURUSD")
    check("EURUSD in sane range", 0.5 < eurusd < 2.0, f"{eurusd:.4f}")

    eurgbp = get_latest_rate("EURGBP")
    check("EURGBP in sane range", 0.5 < eurgbp < 2.0, f"{eurgbp:.4f}")

    # Date lookup
    rate_date = rates[0].date.isoformat()
    rate_on_date = get_rate_for_date("EURUSD", rate_date)
    check("get_rate_for_date returns value", rate_on_date > 0, f"{rate_on_date:.4f}")

    # USD to EUR conversion
    eur_val = usd_to_eur(100.0)
    check("usd_to_eur(100) > 0", eur_val > 0, f"€{eur_val:.2f}")
    check("usd_to_eur(100) < 200", eur_val < 200, "sanity bound")


# ---------------------------------------------------------------------------
# Price Fetcher tests
# ---------------------------------------------------------------------------


def test_price_fetcher() -> None:
    """Test price fetching from Tiingo and yfinance."""
    print("\n=== Price Fetcher ===")

    # yfinance — always available, no API key needed
    aapl_yf = fetch_yfinance_price("AAPL")
    check("yfinance AAPL returns price", aapl_yf is not None)
    if aapl_yf:
        check("yfinance AAPL > $50", aapl_yf > 50, f"${aapl_yf:.2f}")

    # Silver commodity (SI=F futures)
    silver = fetch_yfinance_price("SI")
    check("yfinance silver (SI=F) returns price", silver is not None)
    if silver:
        check("Silver price > $10", silver > 10, f"${silver:.2f}")

    # Tiingo — only if API key is set
    tiingo_key = os.environ.get("TIINGO_API_KEY", "")
    if tiingo_key:
        aapl_ti = fetch_tiingo_price("AAPL")
        check("Tiingo AAPL returns price", aapl_ti is not None)
        if aapl_ti:
            check("Tiingo AAPL > $50", aapl_ti > 50, f"${aapl_ti:.2f}")
    else:
        print("  [SKIP] Tiingo tests — TIINGO_API_KEY not set")

    # Combined fetch with fallback
    price, source = fetch_price("MSFT")
    check("fetch_price MSFT returns price", price is not None, f"source={source}")
    if price:
        check("MSFT > $100", price > 100, f"${price:.2f}")

    # Price storage and retrieval (needs a price + FX rate to be stored)
    from db import db_conn  # noqa: PLC0415

    with db_conn() as conn:
        today = date.today().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO price_history"
            " (ticker, date, close_usd, close_eur, source)"
            " VALUES (?, ?, ?, ?, ?)",
            ("TEST", today, 150.0, 135.0, "test"),
        )

    pp = get_current_price("TEST")
    check("get_current_price retrieves stored price", pp is not None)
    if pp:
        check("Stored price matches", pp.close_usd == 150.0, f"${pp.close_usd}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all integration tests."""
    print(f"WealthAgent Data Fetcher Integration Tests — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"Temp DB: {os.environ['DB_PATH']}")

    test_fx_fetcher()
    test_price_fetcher()

    print(f"\n{'=' * 40}")
    print(f"Results: {_passed} passed, {_failed} failed")

    # Cleanup
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass

    sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    main()
