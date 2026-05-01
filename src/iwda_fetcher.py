"""IWDA ETF holdings fetcher with a 3-tier fallback chain.

Fetches the top-N holdings of iShares MSCI World UCITS ETF (ISIN IE00B4L5Y983)
from three sources in order:

1. **Primary** — iShares official CSV download
2. **Fallback 1** — justETF holdings page (HTML scrape)
3. **Fallback 2** — stockanalysis.com holdings page (HTML scrape)

If all three live sources fail, the most recent cached snapshot from
``iwda_holdings`` is returned.  Telegram alerts are sent on degraded paths.

Note: justETF renders some content via JavaScript.  The scraper targets
static HTML that is present in the initial server response; if justETF ever
moves the table behind a dynamic JS call the scraper will fail gracefully and
the chain will advance to stockanalysis.com.  The ``_parse_justetf_html()``
function is still testable with canned HTML even if the live fetch is fragile.

CLI usage (inside the container):
    python -m iwda_fetcher
"""

import csv
import io
import logging
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from config.settings import settings
from db import IwdaHolding, db_conn, get_conn
from notifier import send_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 15
_USER_AGENT = "Mozilla/5.0 (compatible; WealthAgent/1.0; +https://github.com/wealthagent)"

_ISHARES_URL = (
    "https://www.ishares.com/uk/individual/en/products/251881/"
    "ishares-msci-world-ucits-etf-acc-fund/1538022227404.ajax"
    "?fileType=csv&fileName=IWDA_holdings&dataType=fund"
)
_JUSTETF_URL = "https://www.justetf.com/en/etf-profile.html?isin=IE00B4L5Y983"
_STOCKANALYSIS_URL = "https://stockanalysis.com/etf/iwda.l/holdings/"

_SOURCE_ISHARES = "iShares CSV"
_SOURCE_JUSTETF = "justETF"
_SOURCE_STOCKANALYSIS = "stockanalysis.com"

# Ticker used when consolidating Alphabet dual-class shares
_ALPHABET_TICKER = "GOOGL"
_ALPHABET_SECONDARY = "GOOG"
_ALPHABET_NAME = "Alphabet Inc"

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _td_text(cells: list, idx: int | None) -> str:
    """Return stripped text from a BeautifulSoup <td> list at *idx*, or '' if absent."""
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx].get_text(strip=True)


# ---------------------------------------------------------------------------
# Private parsers — pure functions, testable with canned input
# ---------------------------------------------------------------------------


def _parse_ishares_csv(text: str) -> list[IwdaHolding]:
    """Parse an iShares CSV holdings export into a list of IwdaHolding objects.

    The CSV has a multi-line preamble before the actual data table.  We skip
    rows until we find the header row (which contains "Ticker" and "Weight (%)").
    Returns an empty list if the expected columns are not found.
    """
    reader = csv.reader(io.StringIO(text))

    # Scan for the header row
    header: list[str] = []
    header_idx = -1
    rows = list(reader)
    for i, row in enumerate(rows):
        # Normalise whitespace in each cell
        normalised = [c.strip() for c in row]
        if "Ticker" in normalised and "Weight (%)" in normalised:
            header = normalised
            header_idx = i
            break

    if header_idx == -1:
        log.warning("iShares CSV: could not locate header row")
        return []

    try:
        ticker_col = header.index("Ticker")
        name_col = header.index("Name")
        weight_col = header.index("Weight (%)")
    except ValueError as exc:
        log.warning("iShares CSV: missing expected column — %s", exc)
        return []

    holdings: list[IwdaHolding] = []
    rank = 0
    now = datetime.now(UTC)

    for row in rows[header_idx + 1 :]:
        # Pad short rows
        while len(row) <= max(ticker_col, name_col, weight_col):
            row.append("")

        ticker = row[ticker_col].strip()
        name = row[name_col].strip()
        weight_str = row[weight_col].strip().replace(",", ".")

        # Skip rows with no ticker or non-numeric weight
        if not ticker or ticker == "-":
            continue
        try:
            weight = float(weight_str)
        except ValueError:
            continue

        rank += 1
        holdings.append(
            IwdaHolding(
                ticker=ticker,
                name=name,
                weight_pct=weight,
                rank=rank,
                fetched_at=now,
            )
        )

    return holdings


def _parse_justetf_html(html: str) -> list[IwdaHolding]:
    """Parse the justETF ETF profile page HTML into IwdaHolding objects.

    Targets the holdings table that justETF renders in the initial HTML
    response.  Returns an empty list if the expected table structure is absent.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the first table that has a "weight" column and identify column indices
    # in a single pass — avoiding a separate table-selection scan.
    table = None
    ticker_idx = name_idx = weight_idx = None
    for t in soup.find_all("table"):
        header_cells = [th.get_text(strip=True) for th in t.find_all("th")]
        t_ticker = t_name = t_weight = None
        for i, h in enumerate(header_cells):
            hl = h.lower()
            if "symbol" in hl or "ticker" in hl:
                t_ticker = i
            elif "name" in hl or "component" in hl or "holding" in hl:
                t_name = i
            elif "weight" in hl:
                t_weight = i
        if t_weight is not None:
            table = t
            ticker_idx, name_idx, weight_idx = t_ticker, t_name, t_weight
            break

    if table is None:
        log.warning("justETF: could not find holdings table with a weight column")
        return []

    holdings: list[IwdaHolding] = []
    rank = 0
    now = datetime.now(UTC)

    for row in table.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if not cells:
            continue

        ticker = _td_text(cells, ticker_idx)
        name = _td_text(cells, name_idx) if name_idx is not None else ""
        weight_raw = _td_text(cells, weight_idx).replace("%", "").replace(",", ".").strip()

        if not ticker or ticker == "-":
            continue
        try:
            weight = float(weight_raw)
        except ValueError:
            continue

        if not name:
            name = ticker

        rank += 1
        holdings.append(
            IwdaHolding(
                ticker=ticker,
                name=name,
                weight_pct=weight,
                rank=rank,
                fetched_at=now,
            )
        )

    return holdings


def _parse_stockanalysis_html(html: str) -> list[IwdaHolding]:
    """Parse the stockanalysis.com ETF holdings page into IwdaHolding objects.

    Returns an empty list if the expected table structure is absent.
    """
    soup = BeautifulSoup(html, "html.parser")

    # stockanalysis renders a standard <table> with thead/tbody
    table = soup.find("table")
    if table is None:
        log.warning("stockanalysis: could not find holdings table")
        return []

    header_cells = [th.get_text(strip=True) for th in table.find_all("th")]
    ticker_idx = name_idx = weight_idx = None
    for i, h in enumerate(header_cells):
        hl = h.lower()
        if hl in ("symbol", "ticker"):
            ticker_idx = i
        elif hl in ("name", "company"):
            name_idx = i
        elif "%" in h or "weight" in hl:
            weight_idx = i

    if weight_idx is None:
        log.warning("stockanalysis: could not identify weight column")
        return []

    holdings: list[IwdaHolding] = []
    rank = 0
    now = datetime.now(UTC)

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        ticker = _td_text(cells, ticker_idx)
        name = _td_text(cells, name_idx) if name_idx is not None else ""
        weight_raw = _td_text(cells, weight_idx).replace("%", "").replace(",", ".").strip()

        if not ticker or ticker == "-":
            continue
        try:
            weight = float(weight_raw)
        except ValueError:
            continue

        if not name:
            name = ticker

        rank += 1
        holdings.append(
            IwdaHolding(
                ticker=ticker,
                name=name,
                weight_pct=weight,
                rank=rank,
                fetched_at=now,
            )
        )

    return holdings


# ---------------------------------------------------------------------------
# Private fetchers — each hits one live source
# ---------------------------------------------------------------------------


def _http_get(url: str) -> requests.Response:
    """Perform a GET request with project-standard headers and timeout."""
    return requests.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=_REQUEST_TIMEOUT,
    )


def _fetch_ishares() -> list[IwdaHolding]:
    """Fetch IWDA holdings from the iShares official CSV export.

    Raises ``RuntimeError`` on HTTP error or if no holdings are parsed.
    """
    log.info("Fetching IWDA holdings from iShares CSV …")
    resp = _http_get(_ISHARES_URL)
    resp.raise_for_status()
    holdings = _parse_ishares_csv(resp.text)
    if not holdings:
        raise RuntimeError("iShares CSV returned no parseable holdings")
    log.info("iShares CSV: parsed %d holdings", len(holdings))
    return holdings


def _fetch_justetf() -> list[IwdaHolding]:
    """Fetch IWDA holdings from the justETF profile page.

    Raises ``RuntimeError`` on HTTP error or if no holdings are parsed.
    """
    log.info("Fetching IWDA holdings from justETF …")
    resp = _http_get(_JUSTETF_URL)
    resp.raise_for_status()
    holdings = _parse_justetf_html(resp.text)
    if not holdings:
        raise RuntimeError("justETF page returned no parseable holdings")
    log.info("justETF: parsed %d holdings", len(holdings))
    return holdings


def _fetch_stockanalysis() -> list[IwdaHolding]:
    """Fetch IWDA holdings from stockanalysis.com.

    Raises ``RuntimeError`` on HTTP error or if no holdings are parsed.
    """
    log.info("Fetching IWDA holdings from stockanalysis.com …")
    resp = _http_get(_STOCKANALYSIS_URL)
    resp.raise_for_status()
    holdings = _parse_stockanalysis_html(resp.text)
    if not holdings:
        raise RuntimeError("stockanalysis.com returned no parseable holdings")
    log.info("stockanalysis.com: parsed %d holdings", len(holdings))
    return holdings


# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------


def most_recent_fetched_at() -> datetime | None:
    """Return the most recent ``fetched_at`` timestamp in ``iwda_holdings``, or None."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return datetime.fromisoformat(row["fetched_at"])


def _load_cached_holdings() -> list[IwdaHolding]:
    """Return all rows from the most recent snapshot in ``iwda_holdings``.

    Returns an empty list if no snapshots exist.
    """
    latest = most_recent_fetched_at()
    if latest is None:
        return []

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, ticker, name, weight_pct, rank, fetched_at"
            " FROM iwda_holdings WHERE fetched_at = ? ORDER BY rank",
            (latest.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    return [
        IwdaHolding(
            id=row["id"],
            ticker=row["ticker"],
            name=row["name"],
            weight_pct=row["weight_pct"],
            rank=row["rank"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_iwda_holdings() -> list[IwdaHolding]:
    """Fetch IWDA top holdings via the 3-tier fallback chain.

    Tries iShares CSV → justETF → stockanalysis.com in order and stops on
    the first successful response.  On degraded paths a Telegram alert is sent.
    If all live sources fail, returns the most recent cached DB snapshot.

    Returns:
        List of IwdaHolding objects (raw, not yet persisted).

    Raises:
        RuntimeError: if all sources AND the cache are unavailable.
    """
    sources = [
        (_SOURCE_ISHARES, _fetch_ishares),
        (_SOURCE_JUSTETF, _fetch_justetf),
        (_SOURCE_STOCKANALYSIS, _fetch_stockanalysis),
    ]

    last_error: Exception | None = None
    primary_name = sources[0][0]

    for i, (source_name, fetcher) in enumerate(sources):
        try:
            holdings = fetcher()
            if i > 0:
                # Degraded path — alert
                send_message(f"⚠️ IWDA fetch fell back from {primary_name} to {source_name}")
            return holdings
        except Exception as exc:  # noqa: BLE001
            log.warning("IWDA source %r failed: %s", source_name, exc)
            last_error = exc

    # All live sources failed — try cache
    cached = _load_cached_holdings()
    if cached:
        log.warning("All IWDA live sources failed; returning cached snapshot")
        send_message(
            "❌ All IWDA sources failed. Returning last cached snapshot."
            " Use /update_iwda to refresh manually."
        )
        return cached

    # Cache also empty
    send_message("❌ All IWDA sources failed and no cached data available.")
    raise RuntimeError(
        f"All IWDA sources failed and no cached data available. Last error: {last_error}"
    )


def save_holdings(
    holdings: list[IwdaHolding],
    fetched_at: datetime | None = None,
) -> None:
    """Persist a snapshot of holdings to the ``iwda_holdings`` table.

    Uses ``INSERT OR IGNORE`` to honour the ``(ticker, fetched_at)`` UNIQUE
    constraint — re-saving the same snapshot is a no-op.

    Args:
        holdings: List of IwdaHolding objects to persist.
        fetched_at: Timestamp to stamp every row with.  Defaults to now (UTC).
    """
    if not holdings:
        return

    stamp = fetched_at or datetime.now(UTC)
    stamp_str = stamp.isoformat()

    with db_conn() as conn:
        for h in holdings:
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (h.ticker, h.name, h.weight_pct, h.rank, stamp_str),
            )

    log.info("Saved %d IWDA holdings (fetched_at=%s)", len(holdings), stamp_str)


def fetch_and_save() -> list[IwdaHolding]:
    """Fetch IWDA holdings and persist them.  Returns the fetched list.

    This is the main entry point for the monthly pipeline and ``/update_iwda``.
    """
    holdings = fetch_iwda_holdings()
    save_holdings(holdings)
    return holdings


def get_consolidated_top_n(n: int | None = None) -> list[IwdaHolding]:
    """Return the top-N holdings from the latest DB snapshot, with Alphabet consolidated.

    GOOGL (Class A) and GOOG (Class C) are merged into a single row keyed on
    ``GOOGL``: weights are summed and the lower rank number (better position)
    is kept.

    Args:
        n: Number of holdings to return.  Defaults to ``settings.iwda_top_n``.

    Returns:
        Consolidated list sorted by rank, truncated to *n* entries.
    """
    top_n = n if n is not None else settings.iwda_top_n
    all_holdings = _load_cached_holdings()

    # Merge GOOGL + GOOG
    merged: dict[str, IwdaHolding] = {}
    for h in all_holdings:
        if h.ticker == _ALPHABET_SECONDARY:
            # Merge GOOG into GOOGL
            if _ALPHABET_TICKER in merged:
                existing = merged[_ALPHABET_TICKER]
                merged[_ALPHABET_TICKER] = existing.model_copy(
                    update={
                        "weight_pct": existing.weight_pct + h.weight_pct,
                        "rank": min(existing.rank, h.rank),
                        "name": _ALPHABET_NAME,
                    }
                )
            else:
                # GOOGL not seen yet — park GOOG under GOOGL key
                merged[_ALPHABET_TICKER] = h.model_copy(
                    update={"ticker": _ALPHABET_TICKER, "name": _ALPHABET_NAME}
                )
        elif h.ticker == _ALPHABET_TICKER:
            if _ALPHABET_TICKER in merged:
                # GOOG was already parked; merge in GOOGL now
                existing = merged[_ALPHABET_TICKER]
                merged[_ALPHABET_TICKER] = existing.model_copy(
                    update={
                        "weight_pct": existing.weight_pct + h.weight_pct,
                        "rank": min(existing.rank, h.rank),
                        "name": _ALPHABET_NAME,
                    }
                )
            else:
                merged[_ALPHABET_TICKER] = h.model_copy(update={"name": _ALPHABET_NAME})
        else:
            merged[h.ticker] = h

    sorted_holdings = sorted(merged.values(), key=lambda x: x.rank)
    return sorted_holdings[:top_n]


def compute_changes(top_n: int | None = None) -> dict[str, list[str]]:
    """Compare the two most recent snapshots and report holdings changes.

    Uses a hysteresis band (``settings.iwda_exit_buffer``) so that minor
    rank fluctuations do not trigger spurious exits: a ticker only appears
    in ``exited`` if its current rank exceeds ``top_n + iwda_exit_buffer``.

    Args:
        top_n: Size of the tracked top-N set.  Defaults to ``settings.iwda_top_n``.

    Returns:
        A dict with keys:
        - ``"new"``: tickers that entered the top-N in the latest snapshot.
        - ``"exited"``: tickers that were in prior top-N and are now ranked
          beyond ``top_n + exit_buffer`` (or absent).
        - ``"kept"``: tickers present in both top-N sets.
    """
    resolved_n = top_n if top_n is not None else settings.iwda_top_n
    exit_threshold = resolved_n + settings.iwda_exit_buffer

    # Load the two most recent distinct fetched_at timestamps
    conn = get_conn()
    try:
        ts_rows = conn.execute(
            "SELECT DISTINCT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 2"
        ).fetchall()
    finally:
        conn.close()

    if len(ts_rows) < 2:
        # Not enough snapshots to compare
        return {"new": [], "exited": [], "kept": []}

    latest_ts = ts_rows[0]["fetched_at"]
    prior_ts = ts_rows[1]["fetched_at"]

    def _top_n_tickers(ts: str) -> set[str]:
        conn2 = get_conn()
        try:
            rows = conn2.execute(
                "SELECT ticker FROM iwda_holdings WHERE fetched_at = ? AND rank <= ? ORDER BY rank",
                (ts, resolved_n),
            ).fetchall()
        finally:
            conn2.close()
        return {r["ticker"] for r in rows}

    def _rank_map(ts: str) -> dict[str, int]:
        conn2 = get_conn()
        try:
            rows = conn2.execute(
                "SELECT ticker, rank FROM iwda_holdings WHERE fetched_at = ?",
                (ts,),
            ).fetchall()
        finally:
            conn2.close()
        return {r["ticker"]: r["rank"] for r in rows}

    prior_top = _top_n_tickers(prior_ts)
    current_top = _top_n_tickers(latest_ts)
    current_ranks = _rank_map(latest_ts)

    new = sorted(current_top - prior_top)
    kept = sorted(current_top & prior_top)

    # Exited = was in prior top-N AND now ranked beyond threshold (or absent)
    exited: list[str] = []
    for ticker in sorted(prior_top - current_top):
        current_rank = current_ranks.get(ticker)
        if current_rank is None or current_rank > exit_threshold:
            exited.append(ticker)

    return {"new": new, "exited": exited, "kept": kept}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Fetch and persist IWDA holdings, then print a one-line summary."""
    holdings = fetch_and_save()
    print(f"IWDA: saved {len(holdings)} holdings (top ticker: {holdings[0].ticker})")


if __name__ == "__main__":
    main()
