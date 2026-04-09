"""FX rate fetcher — pulls EUR exchange rates from the ECB daily feed.

Stores rates in the ``fx_rates`` table.  Provides helpers to look up
the latest rate or the rate closest to a given date.

CLI usage (inside the container):
    python -m fx_fetcher
"""

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

from db import FxRate, db_conn, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_REQUEST_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Fetch & store
# ---------------------------------------------------------------------------


def fetch_ecb_rates() -> list[FxRate]:
    """Fetch the latest ECB daily reference rates and store them in the DB.

    Returns the list of FxRate objects that were inserted/updated.
    """
    log.info("Fetching ECB daily FX rates …")
    resp = requests.get(ECB_DAILY_URL, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)

    # ECB XML namespace — default ns is the eurofxref vocabulary
    ns = {
        "gesmes": "http://www.gesmes.org/xml/2002-08-01",
        "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
    }

    cube_time = root.find(".//ecb:Cube/ecb:Cube[@time]", ns)
    if cube_time is None:
        raise ValueError("Could not find dated Cube element in ECB XML")

    rate_date = date.fromisoformat(cube_time.attrib["time"])
    rates: list[FxRate] = []

    for cube in cube_time.findall("ecb:Cube[@currency]", ns):
        currency = cube.attrib["currency"]
        pair = f"EUR{currency}"
        rate_val = float(cube.attrib["rate"])
        rates.append(FxRate(date=rate_date, pair=pair, rate=rate_val))

    if not rates:
        log.warning("ECB feed returned no currency rates")
        return []

    with db_conn() as conn:
        for fx in rates:
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
                (fx.date.isoformat(), fx.pair, fx.rate),
            )

    log.info("Stored %d FX rates for %s", len(rates), rate_date.isoformat())
    return rates


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_latest_rate(pair: str) -> float:
    """Return the most recent rate for a currency pair (e.g. 'EURUSD').

    Raises ``ValueError`` if the pair has never been fetched.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT rate FROM fx_rates WHERE pair = ? ORDER BY date DESC LIMIT 1",
            (pair,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"No FX rate found for pair {pair!r}")
    return float(row["rate"])


def get_rate_for_date(pair: str, target: str | date) -> float:
    """Return the rate for *pair* on or before *target*.

    If the exact date isn't available (weekend / holiday), the most recent
    prior rate is returned.  Raises ``ValueError`` if nothing is found.
    """
    if isinstance(target, date):
        target_str = target.isoformat()
    else:
        target_str = target

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT rate FROM fx_rates WHERE pair = ? AND date <= ?"
            " ORDER BY date DESC LIMIT 1",
            (pair, target_str),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(
            f"No FX rate found for {pair!r} on or before {target_str}"
        )
    return float(row["rate"])


def usd_to_eur(amount_usd: float, on_date: str | date | None = None) -> float:
    """Convert a USD amount to EUR.

    Uses the EURUSD rate (EUR 1 = X USD), so EUR = USD / rate.
    If *on_date* is given, uses the rate for that date; otherwise the latest.
    """
    if on_date is not None:
        rate = get_rate_for_date("EURUSD", on_date)
    else:
        rate = get_latest_rate("EURUSD")
    return amount_usd / rate


def gbp_to_eur(amount_gbp: float, on_date: str | date | None = None) -> float:
    """Convert a GBP amount to EUR.

    Uses the EURGBP rate (EUR 1 = X GBP), so EUR = GBP / rate.
    """
    if on_date is not None:
        rate = get_rate_for_date("EURGBP", on_date)
    else:
        rate = get_latest_rate("EURGBP")
    return amount_gbp / rate


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Fetch and display current ECB rates."""
    rates = fetch_ecb_rates()
    if not rates:
        print("No rates fetched.")
        return

    print(f"\nECB rates for {rates[0].date.isoformat()}")
    print("-" * 30)
    # Show a curated set of pairs useful for the portfolio
    highlight = {"EURUSD", "EURGBP", "EURCHF", "EURJPY", "EURSEK", "EURNOK"}
    for fx in sorted(rates, key=lambda r: r.pair):
        marker = " *" if fx.pair in highlight else ""
        print(f"  {fx.pair:8s}  {fx.rate:.4f}{marker}")

    # Print the headline conversions
    try:
        eurusd = get_latest_rate("EURUSD")
        print(f"\n  1 EUR = {eurusd:.4f} USD")
        print(f"  1 USD = {1 / eurusd:.4f} EUR")
    except ValueError:
        pass

    try:
        eurgbp = get_latest_rate("EURGBP")
        print(f"  1 EUR = {eurgbp:.4f} GBP")
        print(f"  1 GBP = {1 / eurgbp:.4f} EUR")
    except ValueError:
        pass


if __name__ == "__main__":
    main()
