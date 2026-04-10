"""Unit tests for context_builder.py."""

import json
from datetime import date, datetime, timedelta

from db import db_conn

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_holding(
    ticker: str, shares: float = 10.0, entry_eur: float = 100.0, pool: str = "long_term"
) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, shares, entry_eur, 1.1, "2024-01-01", pool),
        )


def _seed_price(ticker: str, close_eur: float, days_ago: int = 0) -> None:
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history (ticker, date, close_eur, close_usd, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (ticker, d, close_eur, close_eur * 1.1, "test"),
        )


def _seed_fx(pair: str, rate: float) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), pair, rate),
        )


def _seed_fundamentals(ticker: str, pe: float | None = 25.0, sector: str = "Technology") -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fundamentals"
            " (ticker, fetched_at, pe_ratio, revenue_growth, profit_margin,"
            "  debt_to_equity, dividend_yield, market_cap, sector, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, datetime.now().isoformat(), pe, 0.15, 0.25, 150.0, 0.005, 3e12, sector, "{}"),
        )


def _seed_signal(
    tickers: list[str], sentiment: str = "positive", confidence: float = 0.8, days_ago: int = 0
) -> None:
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    with db_conn() as conn:
        art_id = conn.execute(
            "INSERT INTO news_articles (url, title, source, published_at, processed)"
            " VALUES (?, ?, ?, ?, 1)",
            (f"http://test.com/{ts}", "Test article", "TestSource", ts),
        ).lastrowid
        conn.execute(
            "INSERT INTO news_signals"
            " (article_id, tickers, sentiment, catalyst, summary, confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (art_id, json.dumps(tickers), sentiment, "earnings", "Test summary", confidence, ts),
        )


def _seed_alert(ticker: str, alert_type: str = "price_drop", details: str = "dropped 12%") -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), ticker, alert_type, details),
        )


def _seed_tax_year(gains: float = 500.0, used: float = 500.0) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tax_year (year, realized_gains_eur, exemption_used)"
            " VALUES (?, ?, ?)",
            (datetime.now().year, gains, used),
        )


def _seed_screener(ticker: str, score: float = 7.5) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO screener_candidates"
            " (ticker, llm_score, llm_thesis, sector, revenue_growth, status)"
            " VALUES (?, ?, ?, ?, ?, 'pending')",
            (ticker, score, "Strong growth", "Technology", 0.30),
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_is_stale_true():
    from context_builder import _is_stale

    old_date = date.today() - timedelta(days=5)
    assert _is_stale(old_date) is True


def test_is_stale_false():
    from context_builder import _is_stale

    recent_date = date.today()
    assert _is_stale(recent_date) is False


def test_is_stale_none():
    from context_builder import _is_stale

    assert _is_stale(None) is True


def test_is_stale_string():
    from context_builder import _is_stale

    old = (date.today() - timedelta(days=5)).isoformat()
    assert _is_stale(old) is True
    recent = date.today().isoformat()
    assert _is_stale(recent) is False


def test_is_stale_with_reference():
    from context_builder import _is_stale

    ref = date(2024, 6, 10)
    assert _is_stale(date(2024, 6, 5), reference=ref) is True
    assert _is_stale(date(2024, 6, 9), reference=ref) is False


def test_fmt_eur():
    from context_builder import _fmt_eur

    assert _fmt_eur(None) == "—"
    assert _fmt_eur(1234.56) == "€1,234.56"
    assert _fmt_eur(0.0) == "€0.00"


def test_fmt_pct():
    from context_builder import _fmt_pct

    assert _fmt_pct(None) == "—"
    assert _fmt_pct(12.5) == "+12.5%"
    assert _fmt_pct(-3.2) == "-3.2%"
    assert _fmt_pct(0.0) == "+0.0%"


def test_fmt_fundamentals_pct():
    from context_builder import _fmt_fundamentals_pct

    assert _fmt_fundamentals_pct(None) == "—"
    assert _fmt_fundamentals_pct(0.15) == "15%"


def test_fmt_cap():
    from context_builder import _fmt_cap

    assert _fmt_cap(None) == "—"
    assert _fmt_cap(3e12) == "$3.0T"
    assert _fmt_cap(1.5e9) == "$1.5B"
    assert _fmt_cap(500e6) == "$500M"
    assert _fmt_cap(50000) == "$50,000"


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------


def test_get_holdings_with_prices_empty():
    from context_builder import _get_holdings_with_prices

    assert _get_holdings_with_prices() == []


def test_get_holdings_with_prices_with_data():
    from context_builder import _get_holdings_with_prices

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    result = _get_holdings_with_prices()
    assert len(result) == 1
    h = result[0]
    assert h["ticker"] == "AAPL"
    assert h["shares"] == 10.0
    assert h["total_cost_eur"] == 1000.0
    assert h["current_value_eur"] == 1200.0
    assert h["pnl_eur"] == 200.0
    assert abs(h["pnl_pct"] - 20.0) < 0.01


def test_get_holdings_with_prices_no_price():
    from context_builder import _get_holdings_with_prices

    _seed_holding("AAPL")
    result = _get_holdings_with_prices()
    assert len(result) == 1
    assert result[0]["current_value_eur"] is None
    assert result[0]["pnl_eur"] is None
    assert result[0]["stale"] is True


def test_get_holdings_with_prices_aggregates_lots():
    from context_builder import _get_holdings_with_prices

    _seed_holding("AAPL", shares=5.0, entry_eur=100.0)
    _seed_holding("AAPL", shares=3.0, entry_eur=110.0)
    _seed_price("AAPL", 120.0)
    result = _get_holdings_with_prices()
    assert len(result) == 1
    h = result[0]
    assert h["shares"] == 8.0
    assert h["total_cost_eur"] == 5 * 100.0 + 3 * 110.0


def test_get_holdings_stale_price():
    from context_builder import _get_holdings_with_prices

    _seed_holding("AAPL")
    _seed_price("AAPL", 120.0, days_ago=5)
    result = _get_holdings_with_prices()
    assert result[0]["stale"] is True


def test_get_fx_rates():
    from context_builder import _get_fx_rates

    _seed_fx("EURUSD", 1.0850)
    rates = _get_fx_rates()
    assert rates["EURUSD"] == 1.0850
    assert rates["EURGBP"] is None


def test_get_fundamentals_map():
    from context_builder import _get_fundamentals_map

    _seed_fundamentals("AAPL", pe=25.0)
    result = _get_fundamentals_map()
    assert "AAPL" in result
    assert result["AAPL"]["pe_ratio"] == 25.0


def test_get_fundamentals_map_empty():
    from context_builder import _get_fundamentals_map

    assert _get_fundamentals_map() == {}


def test_get_recent_signals():
    from context_builder import _get_recent_signals

    _seed_signal(["AAPL"], confidence=0.8)
    _seed_signal(["MSFT"], confidence=0.3)  # below threshold
    _seed_signal(["GOOG"], confidence=0.7, days_ago=10)  # too old
    result = _get_recent_signals()
    assert len(result) == 1
    assert "AAPL" in result[0]["tickers"]


def test_get_recent_signals_empty():
    from context_builder import _get_recent_signals

    assert _get_recent_signals() == []


def test_get_active_alerts():
    from context_builder import _get_active_alerts

    _seed_alert("AAPL")
    result = _get_active_alerts()
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_get_active_alerts_empty():
    from context_builder import _get_active_alerts

    assert _get_active_alerts() == []


def test_get_tax_year():
    from context_builder import _get_tax_year

    _seed_tax_year(500.0, 500.0)
    result = _get_tax_year()
    assert result is not None
    assert result["realized_gains_eur"] == 500.0


def test_get_tax_year_empty():
    from context_builder import _get_tax_year

    result = _get_tax_year()
    # conftest clears tax_year, so None expected
    assert result is None


def test_get_screener_candidates():
    from context_builder import _get_screener_candidates

    _seed_screener("PLTR", score=8.5)
    _seed_screener("LOW", score=4.0)  # below threshold
    result = _get_screener_candidates()
    assert len(result) == 1
    assert result[0]["ticker"] == "PLTR"


def test_get_screener_candidates_empty():
    from context_builder import _get_screener_candidates

    assert _get_screener_candidates() == []


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_holdings_empty():
    from context_builder import _format_holdings

    assert _format_holdings([]) == "No holdings."


def test_format_holdings_with_data():
    from context_builder import _format_holdings

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1200.0,
            "pnl_eur": 200.0,
            "pnl_pct": 20.0,
            "stale": False,
        },
    ]
    result = _format_holdings(holdings)
    assert "AAPL" in result
    assert "LONG TERM" in result
    assert "+20.0%" in result


def test_format_holdings_multiple_same_pool():
    from context_builder import _format_holdings

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1200.0,
            "pnl_eur": 200.0,
            "pnl_pct": 20.0,
            "stale": False,
        },
        {
            "ticker": "MSFT",
            "pool": "long_term",
            "shares": 5.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 600.0,
            "pnl_eur": 100.0,
            "pnl_pct": 20.0,
            "stale": False,
        },
    ]
    result = _format_holdings(holdings)
    assert result.count("LONG TERM") == 1  # header appears once
    assert "AAPL" in result
    assert "MSFT" in result


def test_format_holdings_stale():
    from context_builder import _format_holdings

    holdings = [
        {
            "ticker": "XAG",
            "pool": "short_term",
            "shares": 50.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 520.0,
            "pnl_eur": 20.0,
            "pnl_pct": 4.0,
            "stale": True,
        },
    ]
    result = _format_holdings(holdings)
    assert "[STALE]" in result


def test_format_holdings_no_price():
    from context_builder import _format_holdings

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": None,
            "pnl_eur": None,
            "pnl_pct": None,
            "stale": True,
        },
    ]
    result = _format_holdings(holdings)
    assert "AAPL" in result


def test_format_fundamentals_no_data():
    from context_builder import _format_fundamentals

    holdings = [{"ticker": "NEW"}]
    result = _format_fundamentals(holdings, {})
    assert "[NO DATA]" in result


def test_format_fundamentals_with_data():
    from context_builder import _format_fundamentals

    holdings = [{"ticker": "AAPL"}]
    fund_map = {
        "AAPL": {
            "pe_ratio": 25.0,
            "revenue_growth": 0.15,
            "profit_margin": 0.25,
            "debt_to_equity": 150.0,
            "dividend_yield": 0.005,
            "market_cap": 3e12,
            "sector": "Technology",
            "next_earnings": "2025-07-25",
        },
    }
    result = _format_fundamentals(holdings, fund_map)
    assert "P/E=25.0" in result
    assert "Technology" in result


def test_format_fundamentals_missing_fields():
    from context_builder import _format_fundamentals

    holdings = [{"ticker": "XAG"}]
    fund_map = {
        "XAG": {
            "pe_ratio": None,
            "revenue_growth": None,
            "profit_margin": None,
            "debt_to_equity": None,
            "dividend_yield": None,
            "market_cap": None,
            "sector": None,
            "next_earnings": None,
        },
    }
    result = _format_fundamentals(holdings, fund_map)
    assert "P/E=—" in result


def test_format_fundamentals_empty_holdings():
    from context_builder import _format_fundamentals

    assert _format_fundamentals([], {}) == "No holdings."


def test_format_signals_empty():
    from context_builder import _format_signals

    assert _format_signals([]) == "No significant signals this week."


def test_format_signals_with_data():
    from context_builder import _format_signals

    signals = [
        {
            "tickers": json.dumps(["AAPL"]),
            "sentiment": "positive",
            "catalyst": "earnings",
            "summary": "Beat expectations",
            "confidence": 0.85,
            "source": "CNBC",
        },
    ]
    result = _format_signals(signals)
    assert "AAPL" in result
    assert "0.85" in result
    assert "CNBC" in result


def test_format_signals_no_source():
    from context_builder import _format_signals

    signals = [
        {
            "tickers": json.dumps(["MSFT"]),
            "sentiment": "negative",
            "catalyst": "regulation",
            "summary": "Antitrust",
            "confidence": 0.7,
            "source": None,
        },
    ]
    result = _format_signals(signals)
    assert "MSFT" in result
    assert "[" not in result.split("Antitrust")[1]  # no source bracket


def test_format_signals_bad_tickers_json():
    from context_builder import _format_signals

    signals = [
        {
            "tickers": "not-json",
            "sentiment": "neutral",
            "catalyst": None,
            "summary": "Test",
            "confidence": 0.6,
            "source": None,
        },
    ]
    result = _format_signals(signals)
    assert "—" in result  # fallback for unparseable tickers


def test_format_signals_null_fields():
    from context_builder import _format_signals

    signals = [
        {
            "tickers": None,
            "sentiment": None,
            "catalyst": None,
            "summary": None,
            "confidence": None,
            "source": None,
        },
    ]
    result = _format_signals(signals)
    assert "neutral" in result  # default sentiment


def test_format_alerts_empty():
    from context_builder import _format_alerts

    assert _format_alerts([]) == "No active alerts."


def test_format_alerts_with_data():
    from context_builder import _format_alerts

    alerts = [
        {"alert_type": "price_drop", "ticker": "AAPL", "details": "dropped 12%"},
    ]
    result = _format_alerts(alerts)
    assert "PRICE_DROP" in result
    assert "AAPL" in result


def test_format_alerts_null_fields():
    from context_builder import _format_alerts

    alerts = [{"alert_type": None, "ticker": None, "details": None}]
    result = _format_alerts(alerts)
    assert "UNKNOWN" in result


def test_format_tax_year_none():
    from context_builder import _format_tax_year

    assert _format_tax_year(None) == "No tax year data."


def test_format_tax_year_with_data():
    from context_builder import _format_tax_year

    tax = {"realized_gains_eur": 500.0, "exemption_used": 500.0}
    result = _format_tax_year(tax)
    assert "€500.00" in result
    assert "Remaining Exemption" in result


def test_format_budget():
    from context_builder import _format_budget

    result = _format_budget()
    assert "€2,000.00" in result
    assert "€1,500.00" in result
    assert "€500.00" in result


def test_format_screener_empty():
    from context_builder import _format_screener

    assert _format_screener([]) == "No screener candidates."


def test_format_screener_with_data():
    from context_builder import _format_screener

    candidates = [
        {
            "ticker": "PLTR",
            "llm_score": 8.5,
            "sector": "Technology",
            "revenue_growth": 0.45,
            "llm_thesis": "AI platform leader",
        },
    ]
    result = _format_screener(candidates)
    assert "PLTR" in result
    assert "8.5" in result
    assert "45%" in result


def test_format_screener_null_fields():
    from context_builder import _format_screener

    candidates = [
        {
            "ticker": None,
            "llm_score": None,
            "sector": None,
            "revenue_growth": None,
            "llm_thesis": None,
        },
    ]
    result = _format_screener(candidates)
    assert "0.0" in result


# ---------------------------------------------------------------------------
# Integration — build_context
# ---------------------------------------------------------------------------


def test_build_context_empty():
    from context_builder import build_context

    ctx = build_context()
    assert "PORTFOLIO STATE" in ctx
    assert "HOLDINGS" in ctx
    assert "No holdings." in ctx
    assert "MONTHLY BUDGET" in ctx


def test_build_context_full():
    from context_builder import build_context

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    _seed_fx("EURUSD", 1.085)
    _seed_fundamentals("AAPL")
    _seed_signal(["AAPL"], confidence=0.85)
    _seed_alert("AAPL")
    _seed_tax_year(500.0, 500.0)
    _seed_screener("PLTR")

    ctx = build_context()
    assert "PORTFOLIO STATE" in ctx
    assert "AAPL" in ctx
    assert "HOLDINGS" in ctx
    assert "FUNDAMENTALS" in ctx
    assert "RECENT NEWS SIGNALS" in ctx
    assert "ACTIVE ALERTS" in ctx
    assert "TAX YEAR" in ctx
    assert "MONTHLY BUDGET" in ctx
    assert "SCREENER CANDIDATES" in ctx
    assert "PLTR" in ctx


def test_build_context_stale_flagged():
    from context_builder import build_context

    _seed_holding("AAPL")
    _seed_price("AAPL", 120.0, days_ago=5)
    ctx = build_context()
    assert "[STALE]" in ctx


def test_build_context_commodity_included():
    from context_builder import build_context

    _seed_holding("XAG", pool="short_term")
    _seed_price("XAG", 30.0)
    ctx = build_context()
    assert "XAG" in ctx


def test_build_context_pnl_correct():
    from context_builder import build_context

    _seed_holding("MSFT", shares=5.0, entry_eur=200.0)
    _seed_price("MSFT", 250.0)
    ctx = build_context()
    # P&L should be 5 * (250 - 200) = 250
    assert "€250.00" in ctx


# ---------------------------------------------------------------------------
# build_holdings_summary
# ---------------------------------------------------------------------------


def test_build_holdings_summary_empty():
    from context_builder import build_holdings_summary

    assert build_holdings_summary() == "No holdings."


def test_build_holdings_summary_with_data():
    from context_builder import build_holdings_summary

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    summary = build_holdings_summary()
    assert "AAPL" in summary
    assert "Portfolio:" in summary


def test_build_holdings_summary_shorter_than_context():
    from context_builder import build_context, build_holdings_summary

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    assert len(build_holdings_summary()) < len(build_context())


def test_build_holdings_summary_stale():
    from context_builder import build_holdings_summary

    _seed_holding("AAPL")
    _seed_price("AAPL", 120.0, days_ago=5)
    summary = build_holdings_summary()
    assert "[STALE]" in summary


def test_build_holdings_summary_no_price():
    from context_builder import build_holdings_summary

    _seed_holding("AAPL")
    summary = build_holdings_summary()
    assert "AAPL" in summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main(capsys):
    from context_builder import main

    _seed_holding("AAPL")
    main()
    out = capsys.readouterr().out
    assert "PORTFOLIO STATE" in out
