"""Unit tests for context_builder.py."""

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from db import db_conn

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_holding(
    ticker: str,
    shares: float = 10.0,
    entry_eur: float = 100.0,
    pool: str = "long_term",
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


def _seed_signal(
    tickers: list[str],
    sentiment: str = "positive",
    confidence: float = 0.8,
    days_ago: int = 0,
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


def _seed_tax_year(gains: float = 500.0, used: float = 500.0) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tax_year (year, realized_gains_eur, exemption_used)"
            " VALUES (?, ?, ?)",
            (datetime.now().year, gains, used),
        )


def _seed_iwda_holding(
    ticker: str,
    name: str,
    weight_pct: float,
    rank: int,
    fetched_at: str,
) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (ticker, name, weight_pct, rank, fetched_at),
        )


def _seed_iwda_snapshot(
    tickers: list[str],
    fetched_at: str,
    weights: list[float] | None = None,
    names: list[str] | None = None,
) -> None:
    """Seed a complete snapshot with given tickers at fetched_at."""
    for i, ticker in enumerate(tickers):
        weight = weights[i] if weights else 5.0 - i * 0.1
        name = names[i] if names else f"{ticker} Inc"
        _seed_iwda_holding(ticker, name, weight, i + 1, fetched_at)


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


def test_get_tax_year():
    from context_builder import _get_tax_year

    _seed_tax_year(500.0, 500.0)
    result = _get_tax_year()
    assert result is not None
    assert result["realized_gains_eur"] == 500.0


def test_get_tax_year_empty():
    from context_builder import _get_tax_year

    result = _get_tax_year()
    assert result is None


def test_get_iwda_snapshots_empty():
    from context_builder import _get_iwda_snapshots

    current, prior = _get_iwda_snapshots()
    assert current == []
    assert prior == []


def test_get_iwda_snapshots_one():
    from context_builder import _get_iwda_snapshots

    ts = "2025-04-01T00:00:00"
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts)
    current, prior = _get_iwda_snapshots()
    assert len(current) == 2
    assert prior == []


def test_get_iwda_snapshots_two():
    from context_builder import _get_iwda_snapshots

    ts1 = "2025-03-01T00:00:00"
    ts2 = "2025-04-01T00:00:00"
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts1)
    _seed_iwda_snapshot(["AAPL", "NVDA"], ts2)
    current, prior = _get_iwda_snapshots()
    current_tickers = {h["ticker"] for h in current}
    prior_tickers = {h["ticker"] for h in prior}
    assert "NVDA" in current_tickers
    assert "MSFT" in prior_tickers


def test_get_recent_signals_empty():
    from context_builder import _get_recent_signals

    result = _get_recent_signals(set(), set())
    assert result == []


def test_get_recent_signals_filtered_to_held():
    from context_builder import _get_recent_signals

    _seed_signal(["AAPL"], confidence=0.8)
    _seed_signal(["UNKNOWN_CO"], confidence=0.9)  # not held, not top-N
    result = _get_recent_signals({"AAPL"}, set())
    assert len(result) == 1
    assert "AAPL" in result[0]["tickers"]


def test_get_recent_signals_filtered_to_top_n():
    from context_builder import _get_recent_signals

    _seed_signal(["MSFT"], confidence=0.8)
    _seed_signal(["RANDOM_TICKER"], confidence=0.9)
    result = _get_recent_signals(set(), {"MSFT"})
    assert len(result) == 1
    assert "MSFT" in result[0]["tickers"]


def test_get_recent_signals_drops_old():
    from context_builder import _get_recent_signals

    _seed_signal(["AAPL"], confidence=0.9, days_ago=10)  # too old
    result = _get_recent_signals({"AAPL"}, set())
    assert result == []


def test_get_recent_signals_drops_low_confidence():
    from context_builder import _get_recent_signals

    _seed_signal(["AAPL"], confidence=0.3)  # below threshold
    result = _get_recent_signals({"AAPL"}, set())
    assert result == []


def test_get_recent_signals_empty_universe_includes_all():
    """When both held_tickers and top_n_tickers are empty, all signals pass the filter."""
    from context_builder import _get_recent_signals

    _seed_signal(["RANDOM"], confidence=0.9)
    # Empty universe — relevant_tickers is empty, so no intersection check fails
    result = _get_recent_signals(set(), set())
    assert len(result) == 1


def test_get_recent_signals_capped_at_max():
    from context_builder import _get_recent_signals

    # Seed 25 signals all matching AAPL
    for i in range(25):
        ts = (datetime.now() - timedelta(seconds=i)).isoformat()
        with db_conn() as conn:
            art_id = conn.execute(
                "INSERT INTO news_articles (url, title, source, published_at, processed)"
                " VALUES (?, ?, ?, ?, 1)",
                (f"http://test.com/{ts}", "Test", "src", ts),
            ).lastrowid
            conn.execute(
                "INSERT INTO news_signals"
                " (article_id, tickers, sentiment, catalyst, summary, confidence, extracted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (art_id, json.dumps(["AAPL"]), "positive", "news", "Summary", 0.9, ts),
            )
    result = _get_recent_signals({"AAPL"}, set())
    assert len(result) == 20  # _SIGNAL_MAX


def test_get_recent_signals_bad_json_tickers_in_db():
    """Signals with unparseable JSON tickers are included (fallback to empty list)."""
    from context_builder import _get_recent_signals

    ts = datetime.now().isoformat()
    with db_conn() as conn:
        art_id = conn.execute(
            "INSERT INTO news_articles (url, title, source, published_at, processed)"
            " VALUES (?, ?, ?, ?, 1)",
            ("http://bad.com/1", "Test", "src", ts),
        ).lastrowid
        # Insert a signal with non-JSON tickers string directly
        conn.execute(
            "INSERT INTO news_signals"
            " (article_id, tickers, sentiment, catalyst, summary, confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (art_id, "not-valid-json", "positive", "news", "Summary", 0.9, ts),
        )
    # Empty universe → all signals pass; bad JSON hits the except branch
    result = _get_recent_signals(set(), set())
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_change_labels
# ---------------------------------------------------------------------------


def test_build_change_labels_new():
    from context_builder import _build_change_labels

    current = [{"ticker": "NVDA", "rank": 1}, {"ticker": "AAPL", "rank": 2}]
    prior = [{"ticker": "AAPL", "rank": 1}, {"ticker": "MSFT", "rank": 2}]
    labels = _build_change_labels(current, prior, top_n=2, exit_buffer=5)
    assert labels["NVDA"] == "NEW"


def test_build_change_labels_exited():
    from context_builder import _build_change_labels

    current = [
        {"ticker": "AAPL", "rank": 1},
        {"ticker": "MSFT", "rank": 2},
        # GOOG fell to rank 10 (> top_n + buffer)
        {"ticker": "GOOG", "rank": 10},
    ]
    prior = [{"ticker": "AAPL", "rank": 1}, {"ticker": "GOOG", "rank": 2}]
    labels = _build_change_labels(current, prior, top_n=2, exit_buffer=5)
    assert labels["GOOG"] == "EXITED"


def test_build_change_labels_within_buffer_not_exited():
    from context_builder import _build_change_labels

    # GOOG at rank 4 — within buffer (top_n=2 + buffer=5 = 7), so NOT exited
    current = [
        {"ticker": "AAPL", "rank": 1},
        {"ticker": "MSFT", "rank": 2},
        {"ticker": "GOOG", "rank": 4},
    ]
    prior = [{"ticker": "AAPL", "rank": 1}, {"ticker": "GOOG", "rank": 2}]
    labels = _build_change_labels(current, prior, top_n=2, exit_buffer=5)
    assert "GOOG" not in labels


def test_build_change_labels_absent_is_exited():
    from context_builder import _build_change_labels

    current = [{"ticker": "AAPL", "rank": 1}]
    prior = [{"ticker": "AAPL", "rank": 1}, {"ticker": "OLD", "rank": 2}]
    labels = _build_change_labels(current, prior, top_n=2, exit_buffer=5)
    assert labels["OLD"] == "EXITED"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_section():
    from context_builder import _format_section

    result = _format_section("MY TITLE", "some content")
    assert "MY TITLE" in result
    assert "some content" in result
    assert "═" in result


def test_format_iwda_top_n_empty():
    from context_builder import _format_iwda_top_n

    assert _format_iwda_top_n([], top_n=5) == "No data available."


def test_format_iwda_top_n_with_data():
    from context_builder import _format_iwda_top_n

    snapshot = [
        {"ticker": "AAPL", "name": "Apple Inc", "weight_pct": 4.5, "rank": 1},
        {"ticker": "MSFT", "name": "Microsoft Corp", "weight_pct": 3.8, "rank": 2},
    ]
    result = _format_iwda_top_n(snapshot, top_n=5)
    assert "AAPL" in result
    assert "4.50%" in result


def test_format_iwda_top_n_with_labels():
    from context_builder import _format_iwda_top_n

    snapshot = [
        {"ticker": "NVDA", "name": "Nvidia Corp", "weight_pct": 3.0, "rank": 1},
        {"ticker": "AAPL", "name": "Apple Inc", "weight_pct": 4.5, "rank": 2},
    ]
    labels = {"NVDA": "NEW"}
    result = _format_iwda_top_n(snapshot, top_n=5, label_map=labels)
    assert "[NEW]" in result
    assert "NVDA" in result


def test_format_iwda_top_n_truncates_to_top_n():
    from context_builder import _format_iwda_top_n

    snapshot = [
        {"ticker": f"T{i}", "name": f"Corp {i}", "weight_pct": 1.0, "rank": i + 1}
        for i in range(10)
    ]
    result = _format_iwda_top_n(snapshot, top_n=3)
    assert "T0" in result
    assert "T2" in result
    assert "T3" not in result  # beyond top_n=3


def test_format_holdings_with_pct_empty():
    from context_builder import _format_holdings_with_pct

    assert _format_holdings_with_pct([], stocks_total_eur=0.0) == "No holdings."


def test_format_holdings_with_pct_stocks_only():
    from context_builder import _format_holdings_with_pct

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
    result = _format_holdings_with_pct(holdings, stocks_total_eur=1200.0)
    assert "AAPL" in result
    assert "[100.0%]" in result  # 1200 / 1200 = 100%


def test_format_holdings_with_pct_bond_excluded_by_default():
    from context_builder import _format_holdings_with_pct

    holdings = [
        {
            "ticker": "BND",
            "pool": "bond",
            "shares": 5.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 480.0,
            "pnl_eur": -20.0,
            "pnl_pct": -4.0,
            "stale": False,
        },
    ]
    result = _format_holdings_with_pct(holdings, stocks_total_eur=1000.0)
    assert result == "No holdings."


def test_format_holdings_with_pct_bond_no_pct_marker():
    """Bond positions included when include_bonds=True but should show no pct marker."""
    from context_builder import _format_holdings_with_pct

    holdings = [
        {
            "ticker": "BND",
            "pool": "bond",
            "shares": 5.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 480.0,
            "pnl_eur": -20.0,
            "pnl_pct": -4.0,
            "stale": False,
        },
    ]
    result = _format_holdings_with_pct(holdings, stocks_total_eur=1000.0, include_bonds=True)
    assert "BND" in result
    assert "[" not in result  # no pct marker for bonds


def test_format_holdings_with_pct_no_price():
    from context_builder import _format_holdings_with_pct

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
    result = _format_holdings_with_pct(holdings, stocks_total_eur=0.0)
    assert "AAPL" in result


def test_format_holdings_with_pct_stale():
    from context_builder import _format_holdings_with_pct

    holdings = [
        {
            "ticker": "XYZ",
            "pool": "short_term",
            "shares": 5.0,
            "total_cost_eur": 200.0,
            "current_value_eur": 180.0,
            "pnl_eur": -20.0,
            "pnl_pct": -10.0,
            "stale": True,
        },
    ]
    result = _format_holdings_with_pct(holdings, stocks_total_eur=180.0)
    assert "[STALE]" in result


def test_format_holdings_with_pct_multiple_same_pool():
    """Two holdings with the same pool — pool header printed once (covers branch 379->383)."""
    from context_builder import _format_holdings_with_pct

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
    result = _format_holdings_with_pct(holdings, stocks_total_eur=1800.0)
    assert result.count("LONG TERM") == 1  # header appears only once
    assert "AAPL" in result
    assert "MSFT" in result


def test_format_legacy_holdings_empty():
    from context_builder import _format_legacy_holdings

    result = _format_legacy_holdings(
        holdings=[],
        current_top_n_tickers=set(),
        stocks_total_eur=0.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[],
    )
    assert result == "No legacy holdings."


def test_format_legacy_holdings_all_in_top_n():
    from context_builder import _format_legacy_holdings

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 5.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 600.0,
            "pnl_eur": 100.0,
            "pnl_pct": 20.0,
            "stale": False,
        },
    ]
    result = _format_legacy_holdings(
        holdings=holdings,
        current_top_n_tickers={"AAPL"},
        stocks_total_eur=600.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[{"ticker": "AAPL", "rank": 1}],
    )
    assert result == "No legacy holdings."


def test_format_legacy_holdings_flagged_not_in_iwda():
    from context_builder import _format_legacy_holdings

    holdings = [
        {
            "ticker": "OLD_STOCK",
            "pool": "long_term",
            "shares": 5.0,
            "total_cost_eur": 500.0,
            "current_value_eur": 400.0,
            "pnl_eur": -100.0,
            "pnl_pct": -20.0,
            "stale": False,
        },
    ]
    result = _format_legacy_holdings(
        holdings=holdings,
        current_top_n_tickers=set(),
        stocks_total_eur=400.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[],  # not in IWDA at all
    )
    assert "OLD_STOCK" in result
    assert "not in IWDA" in result


def test_format_legacy_holdings_flagged_by_high_rank():
    from context_builder import _format_legacy_holdings

    holdings = [
        {
            "ticker": "FADED",
            "pool": "long_term",
            "shares": 2.0,
            "total_cost_eur": 200.0,
            "current_value_eur": 150.0,
            "pnl_eur": -50.0,
            "pnl_pct": -25.0,
            "stale": False,
        },
    ]
    # FADED has rank 25 > top_n(15) + exit_buffer(5) = 20 → legacy
    result = _format_legacy_holdings(
        holdings=holdings,
        current_top_n_tickers=set(),
        stocks_total_eur=150.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[{"ticker": "FADED", "rank": 25}],
    )
    assert "FADED" in result
    assert "rank 25" in result


def test_format_legacy_holdings_within_buffer_not_flagged():
    from context_builder import _format_legacy_holdings

    holdings = [
        {
            "ticker": "NEARBY",
            "pool": "long_term",
            "shares": 2.0,
            "total_cost_eur": 200.0,
            "current_value_eur": 180.0,
            "pnl_eur": -20.0,
            "pnl_pct": -10.0,
            "stale": False,
        },
    ]
    # NEARBY at rank 18 <= top_n(15) + exit_buffer(5) = 20 → NOT flagged
    result = _format_legacy_holdings(
        holdings=holdings,
        current_top_n_tickers=set(),
        stocks_total_eur=180.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[{"ticker": "NEARBY", "rank": 18}],
    )
    assert result == "No legacy holdings."


def test_format_legacy_holdings_bonds_excluded():
    from context_builder import _format_legacy_holdings

    holdings = [
        {
            "ticker": "BOND_ETF",
            "pool": "bond",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 980.0,
            "pnl_eur": -20.0,
            "pnl_pct": -2.0,
            "stale": False,
        },
    ]
    result = _format_legacy_holdings(
        holdings=holdings,
        current_top_n_tickers=set(),
        stocks_total_eur=0.0,
        top_n=15,
        exit_buffer=5,
        current_snapshot=[],
    )
    assert result == "No legacy holdings."


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
    # Default: stocks=1050, etf=450, buffer=500, total=2000
    assert "€1,050.00" in result
    assert "€450.00" in result
    assert "€500.00" in result
    assert "€2,000.00" in result


# ---------------------------------------------------------------------------
# Tracking error
# ---------------------------------------------------------------------------


def test_compute_tracking_error_insufficient_data_no_iwda_prior():
    from context_builder import _compute_tracking_error

    # No prices seeded at all
    result = _compute_tracking_error([])
    assert "insufficient data" in result


def test_compute_tracking_error_insufficient_data_no_iwda_current():
    from context_builder import _compute_tracking_error

    today = date.today()
    prior_date = today - timedelta(days=30)
    # Only prior seeded, not current
    _seed_price("IWDA.L", 60.0, days_ago=30)

    with patch("context_builder.date") as mock_date:
        mock_date.today.return_value = today
        mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
        result = _compute_tracking_error([], today=today)

    _ = prior_date  # used for context
    assert "insufficient data" in result


def test_compute_tracking_error_no_holdings():
    from context_builder import _compute_tracking_error

    # IWDA.L has both current and 30d-ago prices but no holdings
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 66.0, days_ago=0)

    result = _compute_tracking_error([])
    assert "insufficient data" in result


def test_compute_tracking_error_positive():
    from context_builder import _compute_tracking_error

    today = date.today()
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 63.0, days_ago=0)  # +5%
    _seed_price("AAPL", 100.0, days_ago=30)
    _seed_price("AAPL", 110.0, days_ago=0)  # +10%

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1100.0,
            "pnl_eur": 100.0,
            "pnl_pct": 10.0,
            "stale": False,
        },
    ]

    result = _compute_tracking_error(holdings, today=today)
    assert "Portfolio:" in result
    assert "IWDA.L:" in result
    assert "Tracking:" in result
    assert "+10.0%" in result  # portfolio return
    assert "+5.0%" in result  # IWDA return
    assert "+5.0 pp" in result  # tracking error


def test_compute_tracking_error_negative():
    from context_builder import _compute_tracking_error

    today = date.today()
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 63.0, days_ago=0)  # +5%
    _seed_price("AAPL", 100.0, days_ago=30)
    _seed_price("AAPL", 102.0, days_ago=0)  # +2%

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1020.0,
            "pnl_eur": 20.0,
            "pnl_pct": 2.0,
            "stale": False,
        },
    ]

    result = _compute_tracking_error(holdings, today=today)
    assert "-3.0 pp" in result  # 2% - 5% = -3 pp


def test_compute_tracking_error_excludes_ticker_no_prior():
    from context_builder import _compute_tracking_error

    today = date.today()
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 63.0, days_ago=0)  # +5%
    _seed_price("AAPL", 100.0, days_ago=30)
    _seed_price("AAPL", 110.0, days_ago=0)
    # MSFT has no 30d-ago price — excluded from calc
    _seed_price("MSFT", 200.0, days_ago=0)

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1100.0,
            "pnl_eur": 100.0,
            "pnl_pct": 10.0,
            "stale": False,
        },
        {
            "ticker": "MSFT",
            "pool": "long_term",
            "shares": 5.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1000.0,
            "pnl_eur": 0.0,
            "pnl_pct": 0.0,
            "stale": False,
        },
    ]

    result = _compute_tracking_error(holdings, today=today)
    # MSFT excluded → result is still valid based on AAPL only
    assert "Portfolio:" in result
    assert "+10.0%" in result


def test_compute_tracking_error_bonds_excluded():
    from context_builder import _compute_tracking_error

    today = date.today()
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 63.0, days_ago=0)  # +5%
    _seed_price("AAPL", 100.0, days_ago=30)
    _seed_price("AAPL", 110.0, days_ago=0)  # +10%
    _seed_price("BND", 50.0, days_ago=30)
    _seed_price("BND", 40.0, days_ago=0)  # -20% — would massively distort if included

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": 1100.0,
            "pnl_eur": 100.0,
            "pnl_pct": 10.0,
            "stale": False,
        },
        {
            "ticker": "BND",
            "pool": "bond",
            "shares": 100.0,
            "total_cost_eur": 5000.0,
            "current_value_eur": 4000.0,
            "pnl_eur": -1000.0,
            "pnl_pct": -20.0,
            "stale": False,
        },
    ]

    result = _compute_tracking_error(holdings, today=today)
    # Bond excluded — only AAPL +10% used
    assert "+10.0%" in result
    # If BND were included: (1100+4000)/(1000+5000) - 1 = 5100/6000 - 1 ≈ -15%
    # So +10 confirms bond is excluded
    assert "+5.0 pp" in result


def test_compute_tracking_error_iwda_current_no_eur():
    """Line 264: IWDA.L current price row exists but close_eur is NULL."""
    from context_builder import _compute_tracking_error

    today = date.today()
    # Seed IWDA.L prior price (normal)
    _seed_price("IWDA.L", 60.0, days_ago=30)
    # Seed IWDA.L today with NULL eur
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history (ticker, date, close_usd, close_eur, source)"
            " VALUES (?, ?, ?, NULL, ?)",
            ("IWDA.L", today.isoformat(), 66.0, "test"),
        )

    result = _compute_tracking_error([], today=today)
    assert "insufficient data" in result


def test_compute_tracking_error_holding_no_current_eur():
    """Line 282: holding has prior price but current price has NULL close_eur."""
    from context_builder import _compute_tracking_error

    today = date.today()
    _seed_price("IWDA.L", 60.0, days_ago=30)
    _seed_price("IWDA.L", 63.0, days_ago=0)  # +5%
    _seed_price("AAPL", 100.0, days_ago=30)
    # AAPL current price with NULL close_eur
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history (ticker, date, close_usd, close_eur, source)"
            " VALUES (?, ?, ?, NULL, ?)",
            ("AAPL", today.isoformat(), 110.0, "test"),
        )

    holdings = [
        {
            "ticker": "AAPL",
            "pool": "long_term",
            "shares": 10.0,
            "total_cost_eur": 1000.0,
            "current_value_eur": None,
            "pnl_eur": None,
            "pnl_pct": None,
            "stale": False,
        },
    ]

    result = _compute_tracking_error(holdings, today=today)
    # AAPL excluded due to NULL current EUR → no valid holdings → insufficient
    assert "insufficient data" in result


# ---------------------------------------------------------------------------
# Bond denominator isolation test
# ---------------------------------------------------------------------------


def test_bond_does_not_affect_stocks_only_denominator():
    """Bond positions must not move the stocks-only denominator."""
    from context_builder import build_context

    # Seed a stocks holding
    _seed_holding("AAPL", shares=10.0, entry_eur=100.0, pool="long_term")
    _seed_price("AAPL", 120.0)

    ctx_without_bond = build_context()

    # Now also seed a bond holding
    _seed_holding("BND", shares=100.0, entry_eur=50.0, pool="bond")
    _seed_price("BND", 52.0)

    ctx_with_bond = build_context()

    # Extract stocks-only value from both contexts — should be the same (€1,200.00)
    # Both contexts should show AAPL contributing 100% of stocks
    assert "[100.0%]" in ctx_without_bond
    assert "[100.0%]" in ctx_with_bond


# ---------------------------------------------------------------------------
# Integration — build_context
# ---------------------------------------------------------------------------


def test_build_context_empty():
    from context_builder import build_context

    ctx = build_context()
    assert "PORTFOLIO STATE" in ctx
    assert "IWDA TOP-N (current)" in ctx
    assert "IWDA TOP-N (prior month)" in ctx
    assert "HOLDINGS" in ctx
    assert "LEGACY HOLDINGS" in ctx
    assert "TRACKING ERROR (30D)" in ctx
    assert "TAX YEAR" in ctx
    assert "MONTHLY BUDGET" in ctx
    assert "RECENT NEWS SIGNALS" in ctx


def test_build_context_no_fundamentals_section():
    from context_builder import build_context

    ctx = build_context()
    assert "FUNDAMENTALS" not in ctx


def test_build_context_no_screener_section():
    from context_builder import build_context

    ctx = build_context()
    assert "SCREENER CANDIDATES" not in ctx


def test_build_context_no_active_alerts_section():
    from context_builder import build_context

    ctx = build_context()
    assert "ACTIVE ALERTS" not in ctx


def test_build_context_iwda_no_data():
    from context_builder import build_context

    ctx = build_context()
    assert "No data available." in ctx
    assert "No prior snapshot — first run." in ctx


def test_build_context_iwda_one_snapshot():
    from context_builder import build_context

    ts = "2025-04-01T00:00:00"
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts)
    ctx = build_context()
    assert "No prior snapshot — first run." in ctx
    assert "AAPL" in ctx


def test_build_context_iwda_two_snapshots():
    from context_builder import build_context

    ts1 = "2025-03-01T00:00:00"
    ts2 = "2025-04-01T00:00:00"
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts1)
    _seed_iwda_snapshot(["AAPL", "NVDA"], ts2)
    ctx = build_context()
    assert "AAPL" in ctx
    assert "NVDA" in ctx
    # Prior month shows MSFT
    assert "MSFT" in ctx


def test_build_context_full():
    from context_builder import build_context

    ts1 = "2025-03-01T00:00:00"
    ts2 = "2025-04-01T00:00:00"
    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    _seed_fx("EURUSD", 1.085)
    _seed_signal(["AAPL"], confidence=0.85)
    _seed_tax_year(500.0, 500.0)
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts1)
    _seed_iwda_snapshot(["AAPL", "NVDA"], ts2)

    ctx = build_context()
    assert "PORTFOLIO STATE" in ctx
    assert "AAPL" in ctx
    assert "HOLDINGS" in ctx
    assert "IWDA TOP-N (current)" in ctx
    assert "IWDA TOP-N (prior month)" in ctx
    assert "RECENT NEWS SIGNALS" in ctx
    assert "TAX YEAR" in ctx
    assert "MONTHLY BUDGET" in ctx
    assert "TRACKING ERROR (30D)" in ctx


def test_build_context_stale_flagged():
    from context_builder import build_context

    _seed_holding("AAPL")
    _seed_price("AAPL", 120.0, days_ago=5)
    ctx = build_context()
    assert "[STALE]" in ctx


def test_build_context_signal_filtered_by_universe():
    from context_builder import build_context

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    _seed_signal(["AAPL"], confidence=0.9)  # should be included
    _seed_signal(["RANDOM_XYZ"], confidence=0.95)  # not held, not top-N — excluded

    ctx = build_context()
    # AAPL signal included
    assert "AAPL" in ctx
    # RANDOM_XYZ not in signals section (may be in holdings section as no holding)
    # Check that signal section doesn't reference RANDOM_XYZ
    signals_start = ctx.find("RECENT NEWS SIGNALS")
    signals_section = ctx[signals_start:]
    assert "RANDOM_XYZ" not in signals_section


def test_build_context_pnl_correct():
    from context_builder import build_context

    _seed_holding("MSFT", shares=5.0, entry_eur=200.0)
    _seed_price("MSFT", 250.0)
    ctx = build_context()
    # P&L should be 5 * (250 - 200) = 250
    assert "€250.00" in ctx


def test_build_context_section_order():
    from context_builder import build_context

    ctx = build_context()
    # Verify IWDA sections appear before HOLDINGS
    iwda_pos = ctx.find("IWDA TOP-N (current)")
    holdings_pos = ctx.find("═══ HOLDINGS ═══")
    assert iwda_pos < holdings_pos


def test_build_context_legacy_holdings():
    from context_builder import build_context

    # Ticker held but not in IWDA at all → legacy
    _seed_holding("OLD_CO", shares=5.0, entry_eur=100.0, pool="long_term")
    _seed_price("OLD_CO", 80.0)

    ts = "2025-04-01T00:00:00"
    _seed_iwda_snapshot(["AAPL", "MSFT", "NVDA"], ts)

    ctx = build_context()
    assert "LEGACY HOLDINGS" in ctx
    legacy_start = ctx.find("═══ LEGACY HOLDINGS ═══")
    legacy_section = ctx[legacy_start : legacy_start + 500]
    assert "OLD_CO" in legacy_section
    assert "not in IWDA" in legacy_section


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


def test_build_holdings_summary_portfolio_pct():
    from context_builder import build_holdings_summary

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0)
    _seed_price("AAPL", 120.0)
    summary = build_holdings_summary()
    # AAPL is 100% of stocks-only portfolio
    assert "[100.0%]" in summary


def test_build_holdings_summary_bond_no_pct():
    """Bond holdings should not show a portfolio_pct marker."""
    from context_builder import build_holdings_summary

    _seed_holding("BND", shares=10.0, entry_eur=50.0, pool="bond")
    _seed_price("BND", 52.0)
    summary = build_holdings_summary()
    # BND appears but has no [pct] marker
    assert "BND" in summary
    assert "[" not in summary  # No pct markers (stocks_total_eur=0)


def test_build_holdings_summary_mixed_pools_pct():
    from context_builder import build_holdings_summary

    _seed_holding("AAPL", shares=10.0, entry_eur=100.0, pool="long_term")
    _seed_price("AAPL", 100.0)
    _seed_holding("MSFT", shares=5.0, entry_eur=200.0, pool="long_term")
    _seed_price("MSFT", 200.0)
    # AAPL: 10*100=1000, MSFT: 5*200=1000, total=2000
    # AAPL pct = 50%, MSFT pct = 50%
    summary = build_holdings_summary()
    assert "[50.0%]" in summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main(capsys):
    from context_builder import main

    _seed_holding("AAPL")
    main()
    out = capsys.readouterr().out
    assert "PORTFOLIO STATE" in out
