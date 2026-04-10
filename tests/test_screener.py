"""Unit tests for screener.py."""

import json
from datetime import datetime
from unittest import mock

import pytest

from db import db_conn, get_conn


def _seed_holding(ticker: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, 10.0, 100.0, 1.1, "2024-01-01", "long_term"),
        )


def _mock_fund(ticker: str = "TEST", **overrides):
    """Return a mock Fundamentals object."""
    from fundamentals import Fundamentals

    defaults = {
        "ticker": ticker,
        "fetched_at": datetime.now(),
        "pe_ratio": 25.0,
        "revenue_growth": 0.20,
        "profit_margin": 0.15,
        "free_cash_flow": 1e9,
        "debt_to_equity": 50.0,
        "dividend_yield": 0.005,
        "market_cap": 5e9,
        "sector": "Technology",
        "industry": "Software",
        "country": "US",
    }
    defaults.update(overrides)
    return Fundamentals(**defaults)


def _scored_json(score: float = 7.5, thesis: str = "Strong growth", risk: str = "Valuation"):
    return json.dumps({"score": score, "thesis": thesis, "risk": risk})


# ---------------------------------------------------------------------------
# _get_held_tickers
# ---------------------------------------------------------------------------


def test_get_held_tickers_empty():
    from screener import _get_held_tickers

    assert _get_held_tickers() == set()


def test_get_held_tickers_with_data():
    from screener import _get_held_tickers

    _seed_holding("AAPL")
    _seed_holding("MSFT")
    result = _get_held_tickers()
    assert result == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# screen_us_stocks
# ---------------------------------------------------------------------------


def _patch_finviz(overview_cls):
    """Context manager to mock finvizfinance.screener.overview."""
    mod = mock.MagicMock(Overview=overview_cls)
    return mock.patch.dict(
        "sys.modules",
        {"finvizfinance.screener.overview": mod},
    )


def _make_screen_df(tickers: list[str], empty: bool = False):
    """Build a mock DataFrame for finvizfinance screener results."""
    df = mock.MagicMock()
    df.empty = empty
    df.__getitem__ = lambda self, key: mock.MagicMock(
        tolist=lambda: tickers,
    )
    return df


def test_screen_us_stocks_success():
    import screener

    mock_overview = mock.MagicMock()
    mock_overview.return_value.screener_view.return_value = _make_screen_df(
        ["PLTR", "CRWD", "SNOW"]
    )
    with _patch_finviz(mock_overview):
        result = screener.screen_us_stocks()
    assert "PLTR" in result


def test_screen_us_stocks_excludes_held():
    import screener

    _seed_holding("PLTR")

    mock_overview = mock.MagicMock()
    mock_overview.return_value.screener_view.return_value = _make_screen_df(["PLTR", "CRWD"])
    with _patch_finviz(mock_overview):
        result = screener.screen_us_stocks()
    assert "PLTR" not in result
    assert "CRWD" in result


def test_screen_us_stocks_empty_result():
    import screener

    mock_overview = mock.MagicMock()
    mock_overview.return_value.screener_view.return_value = _make_screen_df([], empty=True)
    with _patch_finviz(mock_overview):
        result = screener.screen_us_stocks()
    assert result == []


def test_screen_us_stocks_none_result():
    import screener

    mock_overview = mock.MagicMock()
    mock_overview.return_value.screener_view.return_value = None

    with _patch_finviz(mock_overview):
        result = screener.screen_us_stocks()
    assert result == []


def test_screen_us_stocks_exception():
    import screener

    err_cls = mock.MagicMock(side_effect=Exception("Finviz down"))
    with _patch_finviz(err_cls):
        result = screener.screen_us_stocks()
    assert result == []


# ---------------------------------------------------------------------------
# _parse_scored_candidate
# ---------------------------------------------------------------------------


def test_parse_scored_candidate_json():
    from screener import _parse_scored_candidate

    result = _parse_scored_candidate(_scored_json())
    assert result.score == 7.5
    assert result.thesis == "Strong growth"


def test_parse_scored_candidate_markdown():
    from screener import _parse_scored_candidate

    content = f"```json\n{_scored_json()}\n```"
    result = _parse_scored_candidate(content)
    assert result.score == 7.5


def test_parse_scored_candidate_markdown_invalid_then_brace():
    from screener import _parse_scored_candidate

    content = "```json\ninvalid\n```\n" + _scored_json()
    result = _parse_scored_candidate(content)
    assert result.score == 7.5


def test_parse_scored_candidate_brace_extraction():
    from screener import _parse_scored_candidate

    content = f"Here is the result: {_scored_json()} and more text"
    result = _parse_scored_candidate(content)
    assert result.score == 7.5


def test_parse_scored_candidate_invalid():
    from screener import _parse_scored_candidate

    with pytest.raises(ValueError, match="Could not parse"):
        _parse_scored_candidate("totally invalid")


def test_parse_scored_candidate_brace_invalid_json():
    from screener import _parse_scored_candidate

    with pytest.raises(ValueError, match="Could not parse"):
        _parse_scored_candidate("some {invalid json} here")


# ---------------------------------------------------------------------------
# score_candidate
# ---------------------------------------------------------------------------


def test_score_candidate_success():
    from screener import score_candidate

    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": _scored_json()}}]}
    with mock.patch("ollama_client.requests.post", return_value=mock_resp):
        result = score_candidate("PLTR", "pe_ratio: 25.0\nsector: Technology")
    assert result.score == 7.5


# ---------------------------------------------------------------------------
# _store_candidate
# ---------------------------------------------------------------------------


def test_store_candidate():
    from screener import ScoredCandidate, _store_candidate

    scored = ScoredCandidate(score=8.0, thesis="Great company", risk="High valuation")
    fund = {
        "market_cap": 5e9,
        "revenue_growth": 0.20,
        "pe_ratio": 25.0,
        "sector": "Tech",
        "country": "US",
        "dividend_yield": 0.005,
        "debt_to_equity": 50.0,
    }
    _store_candidate("PLTR", fund, scored)

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM screener_candidates WHERE ticker = 'PLTR'").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["llm_score"] == 8.0
    assert row["llm_thesis"] == "Great company"
    assert row["llm_risk"] == "High valuation"
    assert row["dividend_yield"] == 0.005


# ---------------------------------------------------------------------------
# _format_fundamentals_for_scoring
# ---------------------------------------------------------------------------


def test_format_fundamentals_for_scoring():
    from screener import _format_fundamentals_for_scoring

    fund = {"pe_ratio": 25.0, "sector": "Technology", "market_cap": 5e9}
    result = _format_fundamentals_for_scoring(fund)
    assert "pe_ratio: 25.0" in result
    assert "sector: Technology" in result


def test_format_fundamentals_for_scoring_empty():
    from screener import _format_fundamentals_for_scoring

    result = _format_fundamentals_for_scoring({})
    assert "No data available" in result


# ---------------------------------------------------------------------------
# run_monthly_screen
# ---------------------------------------------------------------------------


def test_run_monthly_screen_no_candidates(monkeypatch):
    import screener

    monkeypatch.setattr(screener, "screen_us_stocks", lambda: [])
    assert screener.run_monthly_screen() == 0


def test_run_monthly_screen_success(monkeypatch):
    import screener

    monkeypatch.setattr(screener, "screen_us_stocks", lambda: ["PLTR"])
    monkeypatch.setattr(screener, "fetch_fundamentals", lambda t: _mock_fund(t))

    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": _scored_json()}}]}
    with mock.patch("ollama_client.requests.post", return_value=mock_resp):
        count = screener.run_monthly_screen()
    assert count == 1

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM screener_candidates WHERE ticker = 'PLTR'").fetchone()
    finally:
        conn.close()
    assert row is not None


def test_run_monthly_screen_no_fundamentals(monkeypatch):
    import screener

    monkeypatch.setattr(screener, "screen_us_stocks", lambda: ["UNKNOWN"])
    monkeypatch.setattr(screener, "fetch_fundamentals", lambda t: None)
    assert screener.run_monthly_screen() == 0


def test_run_monthly_screen_high_dividend(monkeypatch):
    import screener

    # dividend_yield_max is 2.0 in settings, stored as percentage
    # fund dividend_yield 0.05 = 5% > 2% max
    monkeypatch.setattr(screener, "screen_us_stocks", lambda: ["DIV"])
    monkeypatch.setattr(
        screener,
        "fetch_fundamentals",
        lambda t: _mock_fund(t, dividend_yield=0.05),
    )
    assert screener.run_monthly_screen() == 0


def test_run_monthly_screen_scoring_failure(monkeypatch):
    import screener

    monkeypatch.setattr(screener, "screen_us_stocks", lambda: ["FAIL"])
    monkeypatch.setattr(screener, "fetch_fundamentals", lambda t: _mock_fund(t))
    monkeypatch.setattr(
        screener,
        "score_candidate",
        mock.MagicMock(side_effect=Exception("LLM down")),
    )
    assert screener.run_monthly_screen() == 0


def test_run_monthly_screen_none_dividend(monkeypatch):
    import screener

    monkeypatch.setattr(screener, "screen_us_stocks", lambda: ["NODIV"])
    monkeypatch.setattr(
        screener,
        "fetch_fundamentals",
        lambda t: _mock_fund(t, dividend_yield=None),
    )

    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": _scored_json()}}]}
    with mock.patch("ollama_client.requests.post", return_value=mock_resp):
        count = screener.run_monthly_screen()
    assert count == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main(monkeypatch, capsys):
    import screener

    monkeypatch.setattr(screener, "run_monthly_screen", lambda: 5)
    screener.main()
    assert "5 candidate(s)" in capsys.readouterr().out
