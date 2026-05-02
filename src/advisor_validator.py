"""Advisor validator — cross-checks a MonthlyRebalance against DB state and strategy rules.

Each rule returns zero or one error string.  An empty list means the rebalance
passes all checks.

Public API:
    validate(rebalance, now=None) -> list[str]
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import TYPE_CHECKING

from config.settings import settings
from db import get_conn

if TYPE_CHECKING:
    from advisor import MonthlyRebalance

log = logging.getLogger(__name__)

# Tickers known to be ETFs — never allowed in stock_allocation.
_ETF_BLOCKLIST: frozenset[str] = frozenset(
    {"IWDA", "IWDA.L", "VWCE", "VWRL", "SPY", "QQQ", "VOO", "VTI", "EUNL"}
)

# Valid ticker pattern: uppercase, 1–10 chars, dots and dashes allowed.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Floating-point slack for monetary comparisons (€1).
_MONETARY_SLACK = 1.0

# Maximum allowed deviation of the stocks-only weights sum from 100% (in percentage points).
_WEIGHT_SUM_TOLERANCE_PP = 5.0

# P&L tolerance: 1% relative OR €10 absolute (whichever is greater).
_PNL_RELATIVE_TOLERANCE = 0.01
_PNL_ABSOLUTE_TOLERANCE = 10.0


def _check_stock_allocation_cap(rebalance: MonthlyRebalance) -> str | None:
    """R1 — stock_allocation total must not exceed monthly_stocks_eur."""
    total = sum(a.amount_eur for a in rebalance.stock_allocation)
    cap = settings.monthly_stocks_eur
    if total > cap + _MONETARY_SLACK:
        return f"stock allocation €{total:.0f} exceeds cap €{cap:.0f}"
    return None


def _check_buffer_cap(rebalance: MonthlyRebalance) -> str | None:
    """R2 — buffer_recommendation.amount_eur must not exceed monthly_buffer_eur."""
    amount = rebalance.buffer_recommendation.amount_eur
    cap = settings.monthly_buffer_eur
    if amount > cap + _MONETARY_SLACK:
        return f"buffer €{amount:.0f} exceeds cap €{cap:.0f}"
    return None


def _check_no_etf_in_allocation(rebalance: MonthlyRebalance) -> list[str]:
    """R3 — stock_allocation must not include any known ETF tickers."""
    errors: list[str] = []
    for alloc in rebalance.stock_allocation:
        if alloc.ticker.upper() in _ETF_BLOCKLIST:
            errors.append(f"stock_allocation includes ETF ticker {alloc.ticker}")
    return errors


def _check_sell_tickers_held(rebalance: MonthlyRebalance) -> list[str]:
    """R4 — every sell recommendation must be for a currently-held ticker."""
    if not rebalance.sell_recommendations:
        return []

    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM holdings").fetchall()
    finally:
        conn.close()

    held = {row["ticker"] for row in rows}
    errors: list[str] = []
    for sell in rebalance.sell_recommendations:
        if sell.ticker not in held:
            errors.append(f"sell {sell.ticker}: not currently held")
    return errors


def _check_ticker_shape(rebalance: MonthlyRebalance) -> list[str]:
    """R5 — all tickers must match the valid ticker pattern."""
    all_tickers: list[str] = []

    all_tickers.extend(pos.ticker for pos in rebalance.iwda_top_n)
    all_tickers.extend(entry.ticker for entry in rebalance.portfolio_vs_index)
    all_tickers.extend(alloc.ticker for alloc in rebalance.stock_allocation)
    all_tickers.extend(leg.ticker for leg in rebalance.legacy_holdings)
    all_tickers.extend(sell.ticker for sell in rebalance.sell_recommendations)

    invalid: list[str] = []
    seen: set[str] = set()
    for ticker in all_tickers:
        if ticker not in seen and not _TICKER_RE.match(ticker):
            invalid.append(ticker)
            seen.add(ticker)

    if invalid:
        return [f"invalid ticker format: {', '.join(invalid)}"]
    return []


def _check_stocks_weight_sum(rebalance: MonthlyRebalance) -> str | None:
    """R6 — stocks-only portfolio weights should sum to approximately 100%.

    Skip if portfolio is empty (all portfolio_pct == 0).
    """
    pcts = [entry.portfolio_pct for entry in rebalance.portfolio_vs_index]
    if not pcts:
        return None

    positive = [p for p in pcts if p > 0]
    if not positive:
        # Empty portfolio — skip
        return None

    total = sum(positive)
    if abs(total - 100.0) > _WEIGHT_SUM_TOLERANCE_PP:
        return (
            f"stocks-only portfolio_pct sum is {total:.1f}% "
            f"(expected 100% ±{_WEIGHT_SUM_TOLERANCE_PP:.0f}pp)"
        )
    return None


def _check_pnl_sanity(
    rebalance: MonthlyRebalance,
    now: date | None = None,
) -> list[str]:
    """R7 — realized_gain_eur in sell recommendations should match DB-derived P&L.

    For each sell, queries holdings and price_history to compute the expected
    realized gain.  Allows ±1% relative OR ±€10 absolute tolerance.

    Silently skips tickers where price data is missing.
    """
    if not rebalance.sell_recommendations:
        return []

    ref_date = (now or date.today()).isoformat()
    conn = get_conn()
    try:
        errors: list[str] = []
        for sell in rebalance.sell_recommendations:
            ticker = sell.ticker

            # Sum cost basis from all lots
            holding_rows = conn.execute(
                "SELECT shares, entry_price_eur FROM holdings WHERE ticker = ?",
                (ticker,),
            ).fetchall()
            if not holding_rows:
                continue

            # Latest price for this ticker up to ref_date
            price_row = conn.execute(
                """
                SELECT close_eur FROM price_history
                WHERE ticker = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (ticker, ref_date),
            ).fetchone()
            if price_row is None or price_row["close_eur"] is None:
                continue

            current_price_eur = price_row["close_eur"]

            # Weighted average entry price
            total_shares = sum(r["shares"] for r in holding_rows)
            total_cost = sum(r["shares"] * r["entry_price_eur"] for r in holding_rows)
            if total_shares == 0:
                continue
            avg_entry = total_cost / total_shares

            db_realized_gain = sell.shares * (current_price_eur - avg_entry)
            llm_gain = sell.realized_gain_eur

            tolerance = max(
                _PNL_ABSOLUTE_TOLERANCE,
                abs(db_realized_gain) * _PNL_RELATIVE_TOLERANCE,
            )
            if abs(llm_gain - db_realized_gain) > tolerance:
                errors.append(
                    f"sell {ticker}: realized gain €{llm_gain:.0f} "
                    f"differs from DB €{db_realized_gain:.0f}"
                )
    finally:
        conn.close()

    return errors


def validate(
    rebalance: MonthlyRebalance,
    now: date | None = None,
) -> list[str]:
    """Validate a MonthlyRebalance against DB state and strategy rules.

    Runs all validation rules (R1–R7) and returns a list of human-readable
    error strings.  An empty list means the rebalance passes all checks.

    Args:
        rebalance: The MonthlyRebalance to validate.
        now: Optional reference date (today by default). Used by R7 for testability.

    Returns:
        List of error strings (empty if all checks pass).
    """
    errors: list[str] = []

    # R1 — stock allocation cap
    r1 = _check_stock_allocation_cap(rebalance)
    if r1:
        errors.append(r1)

    # R2 — buffer cap
    r2 = _check_buffer_cap(rebalance)
    if r2:
        errors.append(r2)

    # R3 — no ETF in stock_allocation
    errors.extend(_check_no_etf_in_allocation(rebalance))

    # R4 — sell tickers must be currently held
    errors.extend(_check_sell_tickers_held(rebalance))

    # R5 — ticker shape
    errors.extend(_check_ticker_shape(rebalance))

    # R6 — stocks-only weight sum
    r6 = _check_stocks_weight_sum(rebalance)
    if r6:
        errors.append(r6)

    # R7 — P&L sanity
    errors.extend(_check_pnl_sanity(rebalance, now=now))

    return errors
