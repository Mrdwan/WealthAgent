"""Context builder — assembles portfolio data into structured text for the advisor LLM.

Queries the database for holdings, prices, fundamentals, signals, alerts,
tax year, and screener candidates, then formats them into a single context
string that contains everything the advisor needs.

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
_ALERT_DAYS = 7
_SCREENER_MIN_SCORE = 6.0
_SCREENER_MAX = 8


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


def _fmt_fundamentals_pct(value: float | None) -> str:
    """Format a fundamentals ratio (stored as 0-1 decimal) as a percentage."""
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


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


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------


def _get_holdings_with_prices() -> list[dict]:
    """Return holdings joined with latest price, with P&L computed.

    Results are grouped by ticker (shares and costs summed across lots).
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


def _get_fundamentals_map() -> dict[str, dict]:
    """Return latest fundamentals for all tickers, keyed by ticker."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM fundamentals f
            INNER JOIN (
                SELECT ticker, MAX(fetched_at) AS max_fetched
                FROM fundamentals GROUP BY ticker
            ) latest ON f.ticker = latest.ticker
                AND f.fetched_at = latest.max_fetched
            """,
        ).fetchall()
    finally:
        conn.close()
    return {row["ticker"]: dict(row) for row in rows}


def _get_recent_signals() -> list[dict]:
    """Return signals from the last 7 days with confidence >= 0.6, max 20."""
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
            LIMIT ?
            """,
            (cutoff, _SIGNAL_MIN_CONFIDENCE, _SIGNAL_MAX),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _get_active_alerts() -> list[dict]:
    """Return alerts from the last 7 days."""
    cutoff = (datetime.now() - timedelta(days=_ALERT_DAYS)).isoformat()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts_log WHERE triggered_at >= ? ORDER BY triggered_at DESC",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


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


def _get_screener_candidates() -> list[dict]:
    """Return pending screener candidates with score >= 6, top 8."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM screener_candidates
            WHERE status = 'pending' AND llm_score >= ?
            ORDER BY llm_score DESC
            LIMIT ?
            """,
            (_SCREENER_MIN_SCORE, _SCREENER_MAX),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_section(title: str, content: str) -> str:
    """Format a titled section."""
    return f"{'═' * 3} {title} {'═' * 3}\n{content}\n"


def _format_holdings(holdings: list[dict]) -> str:
    """Format holdings grouped by pool."""
    if not holdings:
        return "No holdings."

    lines: list[str] = []
    current_pool = ""
    for h in sorted(holdings, key=lambda x: (x["pool"], x["ticker"])):
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
        lines.append(
            f"  {h['ticker']:<8} {h['shares']:>8.2f} shares  "
            f"Cost: {_fmt_eur(h['total_cost_eur'])}  "
            f"Value: {value_str}  "
            f"P&L: {pnl_str}{stale}"
        )

    return "\n".join(lines)


def _format_fundamentals(holdings: list[dict], fund_map: dict[str, dict]) -> str:
    """Format fundamentals for held tickers."""
    tickers = sorted({h["ticker"] for h in holdings})
    if not tickers:
        return "No holdings."

    lines: list[str] = []
    for ticker in tickers:
        f = fund_map.get(ticker)
        if f is None:
            lines.append(f"  {ticker}: [NO DATA]")
            continue

        pe = f"{f['pe_ratio']:.1f}" if f.get("pe_ratio") else "—"
        rg = _fmt_fundamentals_pct(f.get("revenue_growth"))
        pm = _fmt_fundamentals_pct(f.get("profit_margin"))
        de = f"{f['debt_to_equity']:.0f}" if f.get("debt_to_equity") else "—"
        dy = _fmt_fundamentals_pct(f.get("dividend_yield"))
        cap = _fmt_cap(f.get("market_cap"))
        sector = f.get("sector") or "—"
        ne = f.get("next_earnings") or "—"
        lines.append(
            f"  {ticker}: P/E={pe}  RevGr={rg}  Margin={pm}  "
            f"D/E={de}  DivYld={dy}  Cap={cap}  "
            f"Sector={sector}  NextEarn={ne}"
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


def _format_alerts(alerts: list[dict]) -> str:
    """Format active alerts."""
    if not alerts:
        return "No active alerts."

    lines: list[str] = []
    for a in alerts:
        atype = (a.get("alert_type") or "unknown").upper()
        ticker = a.get("ticker") or "—"
        details = a.get("details") or ""
        lines.append(f"  [{atype}] {ticker}: {details}")
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
    """Format monthly budget information."""
    total = settings.monthly_budget_eur
    lt = total * settings.long_term_pct
    st = total * settings.short_term_pct
    return (
        f"  Budget: {_fmt_eur(total)}\n"
        f"  Long-term allocation ({settings.long_term_pct:.0%}): {_fmt_eur(lt)}\n"
        f"  Short-term allocation ({settings.short_term_pct:.0%}): {_fmt_eur(st)}"
    )


def _format_screener(candidates: list[dict]) -> str:
    """Format screener candidates."""
    if not candidates:
        return "No screener candidates."

    lines: list[str] = []
    for c in candidates:
        score = c.get("llm_score") or 0
        ticker = c.get("ticker") or "?"
        sector = c.get("sector") or "—"
        rg = c.get("revenue_growth")
        rg_str = f"RevGr={rg * 100:.0f}%" if rg else "RevGr=—"
        thesis = c.get("llm_thesis") or ""
        lines.append(f'  [{score:.1f}] {ticker} — {sector} — {rg_str} — "{thesis}"')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context() -> str:
    """Assemble the full portfolio context string for the advisor LLM.

    Contains everything the advisor needs to make informed recommendations:
    holdings, fundamentals, signals, alerts, tax position, budget, and
    screener candidates.
    """
    log.info("Building portfolio context…")
    holdings = _get_holdings_with_prices()
    log.info("Holdings: %d positions", len(holdings))
    fx = _get_fx_rates()
    log.info("FX rates: %s", {k: v for k, v in fx.items()})
    fund_map = _get_fundamentals_map()
    log.info("Fundamentals: %d tickers", len(fund_map))
    signals = _get_recent_signals()
    log.info("Recent signals: %d", len(signals))
    alerts = _get_active_alerts()
    log.info("Active alerts: %d", len(alerts))
    tax = _get_tax_year()
    log.info("Tax year record: %s", "found" if tax else "not found")
    candidates = _get_screener_candidates()
    log.info("Screener candidates: %d", len(candidates))

    # Portfolio totals
    total_value = sum(h["current_value_eur"] or 0 for h in holdings)
    total_cost = sum(h["total_cost_eur"] for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    fx_str = "  ".join(
        f"{pair}: {rate:.4f}" if rate else f"{pair}: [NO DATA]" for pair, rate in fx.items()
    )

    sections = [
        _format_section(
            "PORTFOLIO STATE",
            f"  Date: {date.today().isoformat()}\n"
            f"  FX Rates: {fx_str}\n"
            f"  Total Value: {_fmt_eur(total_value)}\n"
            f"  Total Cost: {_fmt_eur(total_cost)}\n"
            f"  Total P&L: {_fmt_eur(total_pnl)} ({_fmt_pct(total_pnl_pct)})\n"
            f"  Positions: {len(holdings)}",
        ),
        _format_section("HOLDINGS", _format_holdings(holdings)),
        _format_section("FUNDAMENTALS", _format_fundamentals(holdings, fund_map)),
        _format_section("RECENT NEWS SIGNALS", _format_signals(signals)),
        _format_section("ACTIVE ALERTS", _format_alerts(alerts)),
        _format_section(f"TAX YEAR {datetime.now().year}", _format_tax_year(tax)),
        _format_section("MONTHLY BUDGET", _format_budget()),
        _format_section("SCREENER CANDIDATES", _format_screener(candidates)),
    ]

    context = "\n".join(sections)
    log.info("Context built: %d chars", len(context))
    return context


def build_holdings_summary() -> str:
    """Build a shorter holdings summary suitable for Telegram status messages."""
    holdings = _get_holdings_with_prices()

    if not holdings:
        return "No holdings."

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
        lines.append(f"  {h['ticker']:<8} {_fmt_eur(h['current_value_eur'])} ({pnl_str}){stale}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Print the full context to stdout."""
    print(build_context())


if __name__ == "__main__":
    main()
