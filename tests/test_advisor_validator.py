"""Unit tests for advisor_validator.py — one test per rule R1–R7, pass and fail."""

from datetime import date

import pytest

import advisor_validator
from db import db_conn

# ---------------------------------------------------------------------------
# Helpers to build MonthlyRebalance fixtures
# ---------------------------------------------------------------------------


def _make_rebalance(**overrides):
    """Build a minimal valid MonthlyRebalance for testing."""
    from advisor import (
        Allocation,
        BufferDecision,
        GapAction,
        GapEntry,
        IwdaPosition,
        MonthlyRebalance,
        TaxSummary,
        TrackingErrorReport,
    )

    base = dict(
        summary="buy AAPL €500",
        report="## Report\n...",
        iwda_top_n=[
            IwdaPosition(rank=1, ticker="AAPL", name="Apple Inc", weight_pct=5.0),
        ],
        portfolio_vs_index=[
            GapEntry(
                ticker="AAPL",
                portfolio_pct=95.0,
                index_pct=5.0,
                gap_pct=90.0,
                action=GapAction.OVERWEIGHT,
            ),
        ],
        stock_allocation=[
            Allocation(ticker="AAPL", amount_eur=500.0, rationale="Underweight vs index"),
        ],
        buffer_recommendation=BufferDecision(
            amount_eur=200.0,
            target="MSFT",
            rationale="Most underweight",
        ),
        legacy_holdings=[],
        sell_recommendations=[],
        tracking_error=TrackingErrorReport(
            portfolio_return_pct=2.5,
            iwda_return_pct=2.0,
            tracking_error_pp=0.5,
            explanation="Slight outperformance.",
        ),
        tax_summary=TaxSummary(
            realized_gains_ytd_eur=0.0,
            exemption_used_eur=0.0,
            exemption_remaining_eur=1270.0,
        ),
    )
    base.update(overrides)
    return MonthlyRebalance(**base)


def _seed_holding(ticker: str, shares: float, entry_price_eur: float, pool: str = "long_term"):
    """Seed a holding row in the test DB."""
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,
                                  purchase_date, pool)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ticker, shares, entry_price_eur, 1.0, "2024-01-01", pool),
        )


def _seed_price(ticker: str, price_eur: float, price_date: str = "2025-01-15"):
    """Seed a price_history row in the test DB."""
    with db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO price_history (ticker, date, close_eur, source)
            VALUES (?, ?, ?, 'tiingo')
            """,
            (ticker, price_date, price_eur),
        )


# ---------------------------------------------------------------------------
# R1 — stock allocation cap
# ---------------------------------------------------------------------------


def test_r1_stock_allocation_within_cap(monkeypatch):
    """R1 passes when total stock allocation is at or below the cap."""
    monkeypatch.setattr(advisor_validator.settings, "monthly_stocks_eur", 1050.0)
    r = _make_rebalance()  # allocation is €500 < €1050
    errors = advisor_validator.validate(r)
    r1_errors = [e for e in errors if "exceeds cap" in e and "stock" in e]
    assert not r1_errors


def test_r1_stock_allocation_at_cap(monkeypatch):
    """R1 passes when total allocation exactly equals the cap."""
    from advisor import Allocation

    monkeypatch.setattr(advisor_validator.settings, "monthly_stocks_eur", 1050.0)
    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="AAPL", amount_eur=1050.0, rationale="Full cap"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r1_errors = [e for e in errors if "exceeds cap" in e and "stock" in e]
    assert not r1_errors


def test_r1_stock_allocation_exceeds_cap(monkeypatch):
    """R1 fails when total exceeds cap by more than €1 slack."""
    from advisor import Allocation

    monkeypatch.setattr(advisor_validator.settings, "monthly_stocks_eur", 1050.0)
    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="AAPL", amount_eur=600.0, rationale="a"),
            Allocation(ticker="MSFT", amount_eur=600.0, rationale="b"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r1_errors = [e for e in errors if "stock allocation" in e and "exceeds cap" in e]
    assert len(r1_errors) == 1
    assert "€1200" in r1_errors[0]
    assert "€1050" in r1_errors[0]


def test_r1_slack_allows_floating_point(monkeypatch):
    """R1 allows total up to cap + €1 (floating-point slack)."""
    from advisor import Allocation

    monkeypatch.setattr(advisor_validator.settings, "monthly_stocks_eur", 1050.0)
    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="AAPL", amount_eur=1050.5, rationale="tiny over"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r1_errors = [e for e in errors if "stock allocation" in e and "exceeds cap" in e]
    assert not r1_errors


# ---------------------------------------------------------------------------
# R2 — buffer cap
# ---------------------------------------------------------------------------


def test_r2_buffer_within_cap(monkeypatch):
    """R2 passes when buffer amount is within cap."""
    monkeypatch.setattr(advisor_validator.settings, "monthly_buffer_eur", 500.0)
    r = _make_rebalance()  # buffer €200 < €500
    errors = advisor_validator.validate(r)
    r2_errors = [e for e in errors if "buffer" in e and "exceeds cap" in e]
    assert not r2_errors


def test_r2_buffer_exceeds_cap(monkeypatch):
    """R2 fails when buffer amount exceeds cap."""
    from advisor import BufferDecision

    monkeypatch.setattr(advisor_validator.settings, "monthly_buffer_eur", 500.0)
    r = _make_rebalance(
        buffer_recommendation=BufferDecision(
            amount_eur=600.0,
            target="SILVER",
            rationale="hedge",
        )
    )
    errors = advisor_validator.validate(r)
    r2_errors = [e for e in errors if "buffer" in e and "exceeds cap" in e]
    assert len(r2_errors) == 1
    assert "€600" in r2_errors[0]
    assert "€500" in r2_errors[0]


def test_r2_buffer_slack(monkeypatch):
    """R2 allows buffer up to cap + €1."""
    from advisor import BufferDecision

    monkeypatch.setattr(advisor_validator.settings, "monthly_buffer_eur", 500.0)
    r = _make_rebalance(
        buffer_recommendation=BufferDecision(
            amount_eur=500.5,
            target="MSFT",
            rationale="tiny over",
        )
    )
    errors = advisor_validator.validate(r)
    r2_errors = [e for e in errors if "buffer" in e and "exceeds cap" in e]
    assert not r2_errors


# ---------------------------------------------------------------------------
# R3 — no ETF in stock_allocation
# ---------------------------------------------------------------------------


def test_r3_no_etf_in_allocation():
    """R3 passes when no ETF tickers appear in stock_allocation."""
    r = _make_rebalance()  # AAPL is fine
    errors = advisor_validator.validate(r)
    r3_errors = [e for e in errors if "ETF ticker" in e]
    assert not r3_errors


@pytest.mark.parametrize(
    "etf_ticker",
    ["IWDA", "IWDA.L", "VWCE", "VWRL", "SPY", "QQQ", "VOO", "VTI", "EUNL"],
)
def test_r3_etf_ticker_rejected(etf_ticker):
    """R3 fails when a known ETF ticker appears in stock_allocation."""
    from advisor import Allocation

    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker=etf_ticker, amount_eur=100.0, rationale="etf buy"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r3_errors = [e for e in errors if "ETF ticker" in e]
    assert len(r3_errors) == 1
    assert etf_ticker in r3_errors[0]


# ---------------------------------------------------------------------------
# R4 — sell tickers must be currently held
# ---------------------------------------------------------------------------


def test_r4_sell_held_ticker():
    """R4 passes when the sell ticker is in holdings."""
    from advisor import SellReason, SellRecommendation

    _seed_holding("AAPL", 10.0, 150.0)
    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="AAPL",
                shares=5.0,
                reason=SellReason.TAX_HARVESTING,
                realized_gain_eur=200.0,
                cgt_due_eur=0.0,
                net_proceeds_eur=950.0,
            )
        ]
    )
    errors = advisor_validator.validate(r)
    r4_errors = [e for e in errors if "not currently held" in e]
    assert not r4_errors


def test_r4_sell_unheld_ticker():
    """R4 fails when selling a ticker not in holdings."""
    from advisor import SellReason, SellRecommendation

    # Only MSFT is seeded — TSLA is not held
    _seed_holding("MSFT", 5.0, 200.0)
    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="TSLA",
                shares=2.0,
                reason=SellReason.CATASTROPHE,
                realized_gain_eur=-500.0,
                cgt_due_eur=0.0,
                net_proceeds_eur=300.0,
            )
        ]
    )
    errors = advisor_validator.validate(r)
    r4_errors = [e for e in errors if "not currently held" in e]
    assert len(r4_errors) == 1
    assert "TSLA" in r4_errors[0]


def test_r4_empty_sell_recommendations():
    """R4 is skipped (no errors) when there are no sell recommendations."""
    r = _make_rebalance(sell_recommendations=[])
    errors = advisor_validator.validate(r)
    r4_errors = [e for e in errors if "not currently held" in e]
    assert not r4_errors


# ---------------------------------------------------------------------------
# R5 — ticker shape
# ---------------------------------------------------------------------------


def test_r5_valid_tickers():
    """R5 passes for normal stock tickers."""
    r = _make_rebalance()
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert not r5_errors


def test_r5_valid_ticker_with_dot():
    """R5 passes for tickers like BRK.B."""
    from advisor import Allocation

    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="BRK.B", amount_eur=100.0, rationale="Berkshire"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert not r5_errors


def test_r5_invalid_ticker_lowercase():
    """R5 fails for lowercase tickers."""
    from advisor import Allocation

    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="aapl", amount_eur=100.0, rationale="lowercase"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert len(r5_errors) == 1
    assert "aapl" in r5_errors[0]


def test_r5_invalid_ticker_too_long():
    """R5 fails for tickers exceeding 10 characters."""
    from advisor import Allocation

    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="TOOLONGTICKERXYZ", amount_eur=100.0, rationale="too long"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert len(r5_errors) == 1


def test_r5_invalid_ticker_starts_with_digit():
    """R5 fails for tickers starting with a digit."""
    from advisor import IwdaPosition

    r = _make_rebalance(
        iwda_top_n=[
            IwdaPosition(rank=1, ticker="1AAPL", name="Fake", weight_pct=5.0),
        ],
    )
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert len(r5_errors) == 1
    assert "1AAPL" in r5_errors[0]


def test_r5_duplicate_invalid_ticker_reported_once():
    """R5 reports each invalid ticker only once even if it appears in multiple fields."""
    from advisor import Allocation, IwdaPosition

    r = _make_rebalance(
        iwda_top_n=[
            IwdaPosition(rank=1, ticker="bad!", name="Bad", weight_pct=5.0),
        ],
        stock_allocation=[
            Allocation(ticker="bad!", amount_eur=100.0, rationale="same bad ticker"),
        ],
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    r5_errors = [e for e in errors if "invalid ticker format" in e]
    assert len(r5_errors) == 1
    # The error should only list "bad!" once
    assert r5_errors[0].count("bad!") == 1


# ---------------------------------------------------------------------------
# R6 — stocks-only weight sum
# ---------------------------------------------------------------------------


def test_r6_weight_sum_near_100():
    """R6 passes when portfolio_pct sums close to 100%."""
    from advisor import GapAction, GapEntry

    r = _make_rebalance(
        portfolio_vs_index=[
            GapEntry(
                ticker="AAPL",
                portfolio_pct=60.0,
                index_pct=5.0,
                gap_pct=55.0,
                action=GapAction.OVERWEIGHT,
            ),
            GapEntry(
                ticker="MSFT",
                portfolio_pct=40.0,
                index_pct=5.0,
                gap_pct=35.0,
                action=GapAction.OVERWEIGHT,
            ),
        ]
    )
    errors = advisor_validator.validate(r)
    r6_errors = [e for e in errors if "portfolio_pct sum" in e]
    assert not r6_errors


def test_r6_empty_portfolio_skipped():
    """R6 is skipped when all portfolio_pct values are zero (empty portfolio)."""
    from advisor import GapAction, GapEntry

    r = _make_rebalance(
        portfolio_vs_index=[
            GapEntry(
                ticker="AAPL",
                portfolio_pct=0.0,
                index_pct=5.0,
                gap_pct=-5.0,
                action=GapAction.NEW,
            ),
        ]
    )
    errors = advisor_validator.validate(r)
    r6_errors = [e for e in errors if "portfolio_pct sum" in e]
    assert not r6_errors


def test_r6_no_portfolio_vs_index_skipped():
    """R6 is skipped when portfolio_vs_index is empty."""
    r = _make_rebalance(portfolio_vs_index=[])
    errors = advisor_validator.validate(r)
    r6_errors = [e for e in errors if "portfolio_pct sum" in e]
    assert not r6_errors


def test_r6_weight_sum_far_from_100():
    """R6 fails when portfolio_pct sums well outside 100% ±5pp."""
    from advisor import GapAction, GapEntry

    r = _make_rebalance(
        portfolio_vs_index=[
            GapEntry(
                ticker="AAPL",
                portfolio_pct=50.0,
                index_pct=5.0,
                gap_pct=45.0,
                action=GapAction.OVERWEIGHT,
            ),
            GapEntry(
                ticker="MSFT",
                portfolio_pct=20.0,
                index_pct=5.0,
                gap_pct=15.0,
                action=GapAction.OVERWEIGHT,
            ),
            # sum = 70%, well outside 95–105%
        ]
    )
    errors = advisor_validator.validate(r)
    r6_errors = [e for e in errors if "portfolio_pct sum" in e]
    assert len(r6_errors) == 1
    assert "70.0%" in r6_errors[0]


# ---------------------------------------------------------------------------
# R7 — P&L sanity
# ---------------------------------------------------------------------------


def test_r7_no_sells_skipped():
    """R7 is skipped when there are no sell recommendations."""
    r = _make_rebalance(sell_recommendations=[])
    errors = advisor_validator.validate(r)
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


def test_r7_sell_matches_db():
    """R7 passes when the LLM's realized_gain matches the DB-derived value."""
    from advisor import SellReason, SellRecommendation

    # Holding: 10 shares @ €100 entry
    _seed_holding("AAPL", 10.0, 100.0)
    # Current price: €120 → gain for 5 shares = 5 × (120 - 100) = €100
    _seed_price("AAPL", 120.0, "2025-01-15")

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="AAPL",
                shares=5.0,
                reason=SellReason.TAX_HARVESTING,
                realized_gain_eur=100.0,  # exact match
                cgt_due_eur=0.0,
                net_proceeds_eur=600.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


def test_r7_sell_gain_within_tolerance():
    """R7 passes when the LLM gain is within €10 of DB value."""
    from advisor import SellReason, SellRecommendation

    _seed_holding("MSFT", 10.0, 200.0)
    _seed_price("MSFT", 220.0, "2025-01-15")
    # DB gain for 10 shares = 10 × (220 - 200) = €200
    # LLM says €205 — within €10

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="MSFT",
                shares=10.0,
                reason=SellReason.TAX_HARVESTING,
                realized_gain_eur=205.0,
                cgt_due_eur=0.0,
                net_proceeds_eur=2200.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


def test_r7_sell_gain_mismatch():
    """R7 fails when LLM gain differs from DB by more than €10 and more than 1%."""
    from advisor import SellReason, SellRecommendation

    _seed_holding("NVDA", 10.0, 100.0)
    _seed_price("NVDA", 200.0, "2025-01-15")
    # DB gain for 10 shares = 10 × (200 - 100) = €1000
    # LLM says €500 — far off

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="NVDA",
                shares=10.0,
                reason=SellReason.CATASTROPHE,
                realized_gain_eur=500.0,  # wrong
                cgt_due_eur=0.0,
                net_proceeds_eur=2000.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert len(r7_errors) == 1
    assert "NVDA" in r7_errors[0]
    assert "€500" in r7_errors[0]
    assert "€1000" in r7_errors[0]


def test_r7_missing_price_data_silently_skipped():
    """R7 silently skips tickers with no price data in price_history."""
    from advisor import SellReason, SellRecommendation

    _seed_holding("GOOGL", 5.0, 100.0)
    # No price seeded → should skip silently

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="GOOGL",
                shares=5.0,
                reason=SellReason.DEEP_INDEX_EXIT,
                realized_gain_eur=9999.0,  # would fail if price was present
                cgt_due_eur=0.0,
                net_proceeds_eur=5000.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


def test_r7_missing_holding_data_silently_skipped():
    """R7 silently skips when the ticker has no holding rows."""
    from advisor import SellReason, SellRecommendation

    # No holding seeded for TSLA, no price seeded either
    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="TSLA",
                shares=2.0,
                reason=SellReason.CATASTROPHE,
                realized_gain_eur=-100.0,
                cgt_due_eur=0.0,
                net_proceeds_eur=300.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


def test_r7_multiple_lots_uses_weighted_average():
    """R7 uses the average entry price across lots, not a single lot."""
    from advisor import SellReason, SellRecommendation

    # Two lots: 5 shares @ €100, 5 shares @ €200 → avg €150
    _seed_holding("META", 5.0, 100.0)
    _seed_holding("META", 5.0, 200.0)
    _seed_price("META", 180.0, "2025-01-15")
    # avg entry = 150, current = 180, gain per share = 30
    # 10 shares sold → DB gain = 10 × (180 - 150) = €300

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="META",
                shares=10.0,
                reason=SellReason.TAX_HARVESTING,
                realized_gain_eur=300.0,  # correct
                cgt_due_eur=0.0,
                net_proceeds_eur=1800.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors


# ---------------------------------------------------------------------------
# validate() integration — multiple rules at once
# ---------------------------------------------------------------------------


def test_validate_passes_clean_rebalance():
    """A clean rebalance with no violations returns an empty error list."""
    r = _make_rebalance(portfolio_vs_index=[])
    errors = advisor_validator.validate(r)
    assert errors == []


def test_validate_returns_all_errors():
    """validate() collects and returns errors from multiple failing rules."""
    from advisor import Allocation, BufferDecision

    # R1 violation (over cap) + R3 violation (ETF) + R2 violation (buffer over cap)
    r = _make_rebalance(
        stock_allocation=[
            Allocation(ticker="IWDA", amount_eur=600.0, rationale="etf"),
            Allocation(ticker="AAPL", amount_eur=600.0, rationale="stock"),
        ],
        buffer_recommendation=BufferDecision(
            amount_eur=600.0,
            target="SPY",
            rationale="another etf",
        ),
        portfolio_vs_index=[],
    )
    errors = advisor_validator.validate(r)
    # R1 should fire (total €1200 > €1050)
    assert any("stock allocation" in e and "exceeds cap" in e for e in errors)
    # R2 should fire (buffer €600 > €500)
    assert any("buffer" in e and "exceeds cap" in e for e in errors)
    # R3 should fire (IWDA is an ETF)
    assert any("ETF ticker" in e for e in errors)


def test_r7_zero_shares_holding_silently_skipped():
    """R7 silently skips when holdings exist but all have 0 shares."""
    from advisor import SellReason, SellRecommendation

    # Insert a holding with 0 shares (edge case)
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,
                                  purchase_date, pool)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("AMZN", 0.0, 100.0, 1.0, "2024-01-01", "long_term"),
        )
    _seed_price("AMZN", 150.0, "2025-01-15")

    r = _make_rebalance(
        sell_recommendations=[
            SellRecommendation(
                ticker="AMZN",
                shares=5.0,
                reason=SellReason.TAX_HARVESTING,
                realized_gain_eur=250.0,
                cgt_due_eur=0.0,
                net_proceeds_eur=750.0,
            )
        ]
    )
    errors = advisor_validator.validate(r, now=date(2025, 1, 15))
    r7_errors = [e for e in errors if "realized gain" in e and "differs from DB" in e]
    assert not r7_errors
