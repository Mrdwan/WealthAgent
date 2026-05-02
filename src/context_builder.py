"""Context builder — assembles portfolio data into structured text for the advisor LLM.

Queries the database for holdings, prices, IWDA index snapshots, signals, tax year,
and budget, then formats them into a single context string reflecting the
IWDA index-mirroring strategy.

CLI usage (inside the container):
    python -m context_builder
"""

import json
import logging
from datetime import date, datetime, timedelta

from config.settings import settings
from db import get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_STALE_DAYS = 3
_SIGNAL_DAYS = 7
_SIGNAL_MIN_CONFIDENCE = 0.6
_SIGNAL_MAX = 20
_TRACKING_DAYS = 30
_IWDA_TICKER = "IWDA.L"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _is_stale(price_date: str | date | None, reference: date | None = None) -> bool:
    """Return True if *price_date* is more than ``_STALE_DAYS`` old."""
    if price_date is None:
        return True
    ref = reference or date.today()
    if isinstance(price_date, str):
        price_date = date.fromisoformat(price_date[:10])
    return (ref - price_date).days > _STALE_DAYS


def _fmt_eur(value: float | None) -> str:
    """Format a EUR amount for display."""
    if value is None:
        return "—"
    return f"€{value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    """Format a percentage for display."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------


def _get_holdings_with_prices() -> list[dict]:
    """Return holdings joined with latest price, with P&L computed.

    Results are grouped by (ticker, pool) with shares and costs summed across lots.
    Includes all pools (long_term, short_term, bond).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.ticker, h.shares, h.entry_price_eur, h.pool,
                p.close_eur AS current_price_eur,
                p.date      AS price_date
            FROM holdings h
            LEFT JOIN price_history p ON p.ticker = h.ticker
                AND p.date = (
                    SELECT MAX(p2.date) FROM price_history p2
                    WHERE p2.ticker = h.ticker
                )
            ORDER BY h.pool, h.ticker
            """,
        ).fetchall()
    finally:
        conn.close()

    # Aggregate by (ticker, pool)
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["ticker"], row["pool"])
        if key not in grouped:
            grouped[key] = {
                "ticker": row["ticker"],
                "pool": row["pool"],
                "shares": 0.0,
                "total_cost_eur": 0.0,
                "current_price_eur": row["current_price_eur"],
                "price_date": row["price_date"],
            }
        entry = grouped[key]
        entry["shares"] += row["shares"]
        entry["total_cost_eur"] += row["shares"] * row["entry_price_eur"]

    # Compute P&L
    result: list[dict] = []
    for entry in grouped.values():
        price = entry["current_price_eur"]
        cost = entry["total_cost_eur"]
        if price is not None and entry["shares"] > 0:
            value = entry["shares"] * price
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost else 0.0
        else:
            value = None
            pnl = None
            pnl_pct = None
        entry["current_value_eur"] = value
        entry["pnl_eur"] = pnl
        entry["pnl_pct"] = pnl_pct
        entry["stale"] = _is_stale(entry["price_date"])
        result.append(entry)

    return result


def _get_fx_rates() -> dict[str, float | None]:
    """Return latest EURUSD and EURGBP rates."""
    conn = get_conn()
    rates: dict[str, float | None] = {"EURUSD": None, "EURGBP": None}
    try:
        for pair in rates:
            row = conn.execute(
                "SELECT rate FROM fx_rates WHERE pair = ? ORDER BY date DESC LIMIT 1",
                (pair,),
            ).fetchone()
            if row:
                rates[pair] = row["rate"]
    finally:
        conn.close()
    return rates


def _get_recent_signals(held_tickers: set[str], top_n_tickers: set[str]) -> list[dict]:
    """Return signals from the last 7 days with confidence >= 0.6, max 20.

    Filters to signals whose tickers list intersects with *held_tickers*
    or *top_n_tickers*.  Signals unrelated to the held/index universe are dropped.
    """
    cutoff = (datetime.now() - timedelta(days=_SIGNAL_DAYS)).isoformat()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT ns.tickers, ns.sentiment, ns.catalyst, ns.summary,
                   ns.confidence, na.source
            FROM news_signals ns
            LEFT JOIN news_articles na ON na.id = ns.article_id
            WHERE ns.extracted_at >= ?
              AND ns.confidence >= ?
            ORDER BY ns.confidence DESC, ns.extracted_at DESC
            """,
            (cutoff, _SIGNAL_MIN_CONFIDENCE),
        ).fetchall()
    finally:
        conn.close()

    relevant_tickers = held_tickers | top_n_tickers
    filtered: list[dict] = []
    for row in rows:
        tickers_raw = row["tickers"] or "[]"
        try:
            signal_tickers = (
                json.loads(tickers_raw) if isinstance(tickers_raw, str) else tickers_raw
            )
        except (json.JSONDecodeError, TypeError):
            signal_tickers = []
        if relevant_tickers and not (set(signal_tickers) & relevant_tickers):
            continue
        filtered.append(dict(row))
        if len(filtered) >= _SIGNAL_MAX:
            break

    return filtered


def _get_tax_year() -> dict | None:
    """Return the current tax year record, or None."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tax_year WHERE year = ?",
            (datetime.now().year,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _get_iwda_snapshots() -> tuple[list[dict], list[dict]]:
    """Return (current_snapshot, prior_snapshot) from iwda_holdings.

    Each snapshot is a list of dicts with keys: ticker, name, weight_pct, rank.
    If only one snapshot exists, prior is empty.
    If no snapshots exist, both are empty.
    """
    conn = get_conn()
    try:
        ts_rows = conn.execute(
            "SELECT DISTINCT fetched_at FROM iwda_holdings ORDER BY fetched_at DESC LIMIT 2"
        ).fetchall()
    finally:
        conn.close()

    if not ts_rows:
        return [], []

    def _load_snapshot(ts: str) -> list[dict]:
        conn2 = get_conn()
        try:
            rows = conn2.execute(
                "SELECT ticker, name, weight_pct, rank FROM iwda_holdings"
                " WHERE fetched_at = ? ORDER BY rank",
                (ts,),
            ).fetchall()
        finally:
            conn2.close()
        return [dict(r) for r in rows]

    current = _load_snapshot(ts_rows[0]["fetched_at"])
    prior = _load_snapshot(ts_rows[1]["fetched_at"]) if len(ts_rows) > 1 else []
    return current, prior


def _compute_tracking_error(
    holdings: list[dict],
    today: date | None = None,
) -> str:
    """Compute 30-day tracking error: portfolio stocks-only vs IWDA.L.

    Returns a formatted string ready for inclusion in the context.
    Excludes bond-pool holdings.
    If IWDA.L has no 30d-ago price, returns "tracking error: insufficient data".
    Tickers with no 30d-ago price are excluded from both sides of the calculation.
    """
    # Lazy imports to avoid circular deps
    from price_fetcher import get_price_on_date  # noqa: PLC0415

    ref_date = today or date.today()
    prior_date = ref_date - timedelta(days=_TRACKING_DAYS)

    # Check IWDA.L first — if missing, bail
    iwda_current = get_price_on_date(_IWDA_TICKER, ref_date)
    iwda_prior = get_price_on_date(_IWDA_TICKER, prior_date)

    if iwda_prior is None or iwda_prior.close_eur is None:
        return "tracking error: insufficient data"
    if iwda_current is None or iwda_current.close_eur is None:
        return "tracking error: insufficient data"

    iwda_return_pct = (iwda_current.close_eur - iwda_prior.close_eur) / iwda_prior.close_eur * 100.0

    # Compute portfolio stocks-only return
    stocks_holdings = [h for h in holdings if h["pool"] != "bond"]

    prior_value = 0.0
    current_value = 0.0
    included = 0

    for h in stocks_holdings:
        prior_pp = get_price_on_date(h["ticker"], prior_date)
        if prior_pp is None or prior_pp.close_eur is None:
            # No 30d-ago price — exclude from both sides
            continue
        current_pp = get_price_on_date(h["ticker"], ref_date)
        if current_pp is None or current_pp.close_eur is None:
            continue

        shares = h["shares"]
        prior_value += shares * prior_pp.close_eur
        current_value += shares * current_pp.close_eur
        included += 1

    if included == 0 or prior_value == 0.0:
        return "tracking error: insufficient data"

    portfolio_return_pct = (current_value - prior_value) / prior_value * 100.0
    tracking_pp = portfolio_return_pct - iwda_return_pct

    sign = "+" if tracking_pp >= 0 else ""
    return (
        f"Portfolio: {_fmt_pct(portfolio_return_pct)}  "
        f"IWDA.L: {_fmt_pct(iwda_return_pct)}  "
        f"Tracking: {sign}{tracking_pp:.1f} pp"
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_section(title: str, content: str) -> str:
    """Format a titled section."""
    return f"{'═' * 3} {title} {'═' * 3}\n{content}\n"


def _format_iwda_top_n(
    snapshot: list[dict],
    top_n: int,
    label_map: dict[str, str] | None = None,
) -> str:
    """Format an IWDA top-N snapshot.

    *label_map* maps ticker -> label string (e.g. "NEW" or "EXITED") for annotation.
    """
    if not snapshot:
        return "No data available."

    lines: list[str] = []
    for h in snapshot[:top_n]:
        ticker = h["ticker"]
        label = f" [{label_map[ticker]}]" if label_map and ticker in label_map else ""
        lines.append(
            f"  {h['rank']:>3}. {ticker:<10} {h['name']:<35} {h['weight_pct']:.2f}%{label}"
        )
    return "\n".join(lines)


def _build_change_labels(
    current_snapshot: list[dict],
    prior_snapshot: list[dict],
    top_n: int,
    exit_buffer: int,
) -> dict[str, str]:
    """Build a ticker -> label map annotating NEW and EXITED tickers.

    NEW: in current top-N, not in prior top-N.
    EXITED: in prior top-N, current rank > top_n + exit_buffer (or not present).
    Returns a dict only for tickers that need a label (unlabelled ones are omitted).
    """
    exit_threshold = top_n + exit_buffer
    prior_tickers = {h["ticker"] for h in prior_snapshot[:top_n]}
    current_tickers = {h["ticker"] for h in current_snapshot[:top_n]}
    current_rank_map = {h["ticker"]: h["rank"] for h in current_snapshot}

    labels: dict[str, str] = {}
    for ticker in current_tickers - prior_tickers:
        labels[ticker] = "NEW"
    for ticker in prior_tickers - current_tickers:
        current_rank = current_rank_map.get(ticker)
        if current_rank is None or current_rank > exit_threshold:
            labels[ticker] = "EXITED"
    return labels


def _format_holdings_with_pct(
    holdings: list[dict],
    stocks_total_eur: float,
    include_bonds: bool = False,
) -> str:
    """Format holdings grouped by pool, including stocks-only portfolio_pct.

    *stocks_total_eur* is used to compute each position's share of the
    stocks-only portfolio (bonds excluded from denominator even if rendered).
    """
    display = [h for h in holdings if include_bonds or h["pool"] != "bond"]
    if not display:
        return "No holdings."

    lines: list[str] = []
    current_pool = ""
    for h in sorted(display, key=lambda x: (x["pool"], x["ticker"])):
        if h["pool"] != current_pool:
            current_pool = h["pool"]
            lines.append(f"\n  {current_pool.upper().replace('_', ' ')}:")

        stale = " [STALE]" if h["stale"] else ""
        value_str = _fmt_eur(h["current_value_eur"])
        pnl_str = (
            f"{_fmt_eur(h['pnl_eur'])} ({_fmt_pct(h['pnl_pct'])})"
            if h["pnl_eur"] is not None
            else "—"
        )
        if h["pool"] != "bond" and stocks_total_eur > 0 and h["current_value_eur"] is not None:
            pct = h["current_value_eur"] / stocks_total_eur * 100.0
            pct_str = f" [{pct:.1f}%]"
        else:
            pct_str = ""
        lines.append(
            f"  {h['ticker']:<8} {h['shares']:>8.2f} shares  "
            f"Cost: {_fmt_eur(h['total_cost_eur'])}  "
            f"Value: {value_str}  "
            f"P&L: {pnl_str}{pct_str}{stale}"
        )

    return "\n".join(lines)


def _format_legacy_holdings(
    holdings: list[dict],
    current_top_n_tickers: set[str],
    stocks_total_eur: float,
    top_n: int,
    exit_buffer: int,
    current_snapshot: list[dict],
) -> str:
    """Format legacy holdings: non-bond holdings NOT in the current IWDA top-N universe.

    A holding is "legacy" if its ticker:
    - Is not in the current top-N consolidated set, AND
    - Either ranks > top_n + exit_buffer in the snapshot, or is absent entirely.

    Bonds are excluded from this section.
    """
    exit_threshold = top_n + exit_buffer
    current_rank_map = {h["ticker"]: h["rank"] for h in current_snapshot}

    legacy = []
    for h in holdings:
        if h["pool"] == "bond":
            continue
        if h["ticker"] in current_top_n_tickers:
            continue
        rank = current_rank_map.get(h["ticker"])
        if rank is not None and rank <= exit_threshold:
            # Within buffer — not flagged as legacy
            continue
        flag = f"rank {rank}" if rank is not None else "not in IWDA"
        legacy.append({**h, "flag": flag})

    if not legacy:
        return "No legacy holdings."

    lines: list[str] = []
    for h in sorted(legacy, key=lambda x: (x["pool"], x["ticker"])):
        value_str = _fmt_eur(h["current_value_eur"])
        pnl_str = (
            f"{_fmt_eur(h['pnl_eur'])} ({_fmt_pct(h['pnl_pct'])})"
            if h["pnl_eur"] is not None
            else "—"
        )
        if stocks_total_eur > 0 and h["current_value_eur"] is not None:
            pct = h["current_value_eur"] / stocks_total_eur * 100.0
            pct_str = f" [{pct:.1f}%]"
        else:
            pct_str = ""
        stale = " [STALE]" if h["stale"] else ""
        lines.append(
            f"  {h['ticker']:<8} {h['shares']:>8.2f} shares  "
            f"Cost: {_fmt_eur(h['total_cost_eur'])}  "
            f"Value: {value_str}  "
            f"P&L: {pnl_str}{pct_str}  "
            f"({h['flag']}){stale}"
        )
    return "\n".join(lines)


def _format_signals(signals: list[dict]) -> str:
    """Format recent news signals."""
    if not signals:
        return "No significant signals this week."

    lines: list[str] = []
    for s in signals:
        tickers_raw = s.get("tickers") or "[]"
        try:
            tickers = json.loads(tickers_raw) if isinstance(tickers_raw, str) else tickers_raw
        except (json.JSONDecodeError, TypeError):
            tickers = []
        ticker_str = ", ".join(tickers) if tickers else "—"
        conf = s.get("confidence") or 0
        sentiment = s.get("sentiment") or "neutral"
        catalyst = s.get("catalyst") or "none"
        summary = s.get("summary") or ""
        source = s.get("source") or ""
        lines.append(
            f"  [{conf:.2f}] {ticker_str} {sentiment} ({catalyst}): {summary}"
            + (f" [{source}]" if source else "")
        )
    return "\n".join(lines)


def _format_tax_year(tax: dict | None) -> str:
    """Format tax year information."""
    if tax is None:
        return "No tax year data."

    gains = tax.get("realized_gains_eur", 0.0)
    used = tax.get("exemption_used", 0.0)
    exemption = settings.annual_exemption
    remaining = max(0.0, exemption - used)
    return (
        f"  Realized Gains: {_fmt_eur(gains)}\n"
        f"  Exemption Used: {_fmt_eur(used)} / {_fmt_eur(exemption)}\n"
        f"  Remaining Exemption: {_fmt_eur(remaining)}"
    )


def _format_budget() -> str:
    """Format monthly IWDA-mirroring budget information."""
    stocks = settings.monthly_stocks_eur
    etf = settings.monthly_etf_eur
    buffer_ = settings.monthly_buffer_eur
    total = stocks + etf + buffer_
    return (
        f"  Stocks (individual): {_fmt_eur(stocks)}\n"
        f"  IWDA ETF: {_fmt_eur(etf)}\n"
        f"  Buffer: {_fmt_eur(buffer_)}\n"
        f"  Total: {_fmt_eur(total)}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context() -> str:
    """Assemble the full portfolio context string for the advisor LLM.

    Contains everything the advisor needs for IWDA index-mirroring decisions:
    portfolio state, IWDA top-N snapshots (current and prior), holdings with
    stocks-only weights, legacy holdings, 30-day tracking error, tax position,
    news signals (filtered to held + top-N universe), and monthly budget.
    """
    log.info("Building portfolio context…")

    holdings = _get_holdings_with_prices()
    log.info("Holdings: %d positions", len(holdings))

    fx = _get_fx_rates()
    log.info("FX rates: %s", {k: v for k, v in fx.items()})

    current_snapshot, prior_snapshot = _get_iwda_snapshots()
    log.info(
        "IWDA snapshots: current=%d, prior=%d",
        len(current_snapshot),
        len(prior_snapshot),
    )

    top_n = settings.iwda_top_n
    exit_buffer = settings.iwda_exit_buffer

    # Stocks-only denominator (explicitly excludes bonds)
    stocks_holdings = [h for h in holdings if h["pool"] != "bond"]
    stocks_total_eur = sum(h["current_value_eur"] or 0.0 for h in stocks_holdings)

    # All-holdings totals (for PORTFOLIO STATE)
    total_cost = sum(h["total_cost_eur"] for h in holdings)
    total_value = sum(h["current_value_eur"] or 0.0 for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    # IWDA top-N tickers (for news filter and legacy detection)
    current_top_n_tickers: set[str] = {h["ticker"] for h in current_snapshot[:top_n]}

    # Held tickers for news filter
    held_tickers: set[str] = {h["ticker"] for h in holdings}

    signals = _get_recent_signals(held_tickers, current_top_n_tickers)
    log.info("Recent signals (filtered): %d", len(signals))

    tax = _get_tax_year()
    log.info("Tax year record: %s", "found" if tax else "not found")

    # Change labels for prior-month annotation
    change_labels: dict[str, str] = {}
    if prior_snapshot:
        change_labels = _build_change_labels(current_snapshot, prior_snapshot, top_n, exit_buffer)

    # PORTFOLIO STATE
    fx_str = "  ".join(
        f"{pair}: {rate:.4f}" if rate else f"{pair}: [NO DATA]" for pair, rate in fx.items()
    )
    portfolio_state = (
        f"  Date: {date.today().isoformat()}\n"
        f"  FX Rates: {fx_str}\n"
        f"  Stocks-only Value: {_fmt_eur(stocks_total_eur)}\n"
        f"  Total Cost: {_fmt_eur(total_cost)}\n"
        f"  Total P&L: {_fmt_eur(total_pnl)} ({_fmt_pct(total_pnl_pct)})\n"
        f"  Positions: {len(holdings)}"
    )

    # IWDA TOP-N (current) — no labels needed, labels go on prior
    current_section = _format_iwda_top_n(current_snapshot, top_n)

    # IWDA TOP-N (prior month)
    if not prior_snapshot:
        prior_section = "No prior snapshot — first run."
    else:
        prior_section = _format_iwda_top_n(prior_snapshot, top_n, label_map=change_labels)

    # HOLDINGS (stocks only, excludes bonds)
    holdings_section = _format_holdings_with_pct(holdings, stocks_total_eur, include_bonds=False)

    # LEGACY HOLDINGS
    legacy_section = _format_legacy_holdings(
        holdings, current_top_n_tickers, stocks_total_eur, top_n, exit_buffer, current_snapshot
    )

    # TRACKING ERROR
    tracking_section = _compute_tracking_error(holdings)

    sections = [
        _format_section("PORTFOLIO STATE", portfolio_state),
        _format_section("IWDA TOP-N (current)", current_section),
        _format_section("IWDA TOP-N (prior month)", prior_section),
        _format_section("HOLDINGS", holdings_section),
        _format_section("LEGACY HOLDINGS", legacy_section),
        _format_section("TRACKING ERROR (30D)", tracking_section),
        _format_section(f"TAX YEAR {datetime.now().year}", _format_tax_year(tax)),
        _format_section("MONTHLY BUDGET", _format_budget()),
        _format_section("RECENT NEWS SIGNALS", _format_signals(signals)),
    ]

    context = "\n".join(sections)
    log.info("Context built: %d chars", len(context))
    return context


def build_holdings_summary() -> str:
    """Build a shorter holdings summary suitable for Telegram status messages.

    Each line includes the stocks-only portfolio_pct for non-bond positions.
    """
    holdings = _get_holdings_with_prices()

    if not holdings:
        return "No holdings."

    stocks_holdings = [h for h in holdings if h["pool"] != "bond"]
    stocks_total_eur = sum(h["current_value_eur"] or 0.0 for h in stocks_holdings)

    total_value = sum(h["current_value_eur"] or 0 for h in holdings)
    total_cost = sum(h["total_cost_eur"] for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    pnl_summary = f"P&L: {_fmt_eur(total_pnl)}, {_fmt_pct(total_pnl_pct)}"
    lines = [
        f"Portfolio: {_fmt_eur(total_value)} ({pnl_summary})",
        "",
    ]
    for h in sorted(holdings, key=lambda x: (x["pool"], x["ticker"])):
        stale = " [STALE]" if h["stale"] else ""
        pnl_str = _fmt_pct(h["pnl_pct"]) if h["pnl_pct"] is not None else "—"
        if h["pool"] != "bond" and stocks_total_eur > 0 and h["current_value_eur"] is not None:
            pct = h["current_value_eur"] / stocks_total_eur * 100.0
            pct_str = f" [{pct:.1f}%]"
        else:
            pct_str = ""
        lines.append(
            f"  {h['ticker']:<8} {_fmt_eur(h['current_value_eur'])} ({pnl_str}){pct_str}{stale}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Print the full context to stdout."""
    print(build_context())


if __name__ == "__main__":
    main()
