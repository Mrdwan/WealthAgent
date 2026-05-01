"""Unit tests for price_fetcher.py."""

from datetime import date, timedelta
from unittest import mock

import pytest

from config.settings import settings
from db import PricePoint, db_conn


def _seed_holding(ticker: str, pool: str = "long_term") -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, 10.0, 150.0, 1.1, "2024-01-01", pool),
        )


def _seed_price(ticker: str, dt: str, usd: float | None, eur: float | None) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history (ticker, date, close_usd, close_eur, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (ticker, dt, usd, eur, "test"),
        )


def _seed_iwda_holding(ticker: str, fetched_at: str, rank: int = 1) -> None:
    """Seed a single row into iwda_holdings for testing."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (ticker, ticker, 1.0, rank, fetched_at),
        )


# --- _tiingo_headers ---


def test_tiingo_headers_no_key(monkeypatch):
    from price_fetcher import _tiingo_headers

    monkeypatch.setattr(settings, "tiingo_api_key", None)
    with pytest.raises(OSError, match="TIINGO_API_KEY is not set"):
        _tiingo_headers()


def test_tiingo_headers_with_key(monkeypatch):
    from price_fetcher import _tiingo_headers

    monkeypatch.setattr(settings, "tiingo_api_key", "abc123")
    headers = _tiingo_headers()
    assert "Token abc123" in headers["Authorization"]


# --- fetch_tiingo_price ---


def test_fetch_tiingo_price_success(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = [{"last": 150.5}]
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") == 150.5


def test_fetch_tiingo_price_empty_data(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = []
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") is None


def test_fetch_tiingo_price_dict_response(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = {"last": 150.5}
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") == 150.5


def test_fetch_tiingo_price_tngo_last(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = [{"tngoLast": 150.5}]
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") == 150.5


def test_fetch_tiingo_price_close_field(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = [{"close": 150.5}]
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") == 150.5


def test_fetch_tiingo_price_no_field(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = [{"ticker": "AAPL"}]  # no price field
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        assert price_fetcher.fetch_tiingo_price("AAPL") is None


def test_fetch_tiingo_price_exception(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    with mock.patch.object(price_fetcher.requests, "get", side_effect=Exception("err")):
        assert price_fetcher.fetch_tiingo_price("AAPL") is None


# --- fetch_yfinance_price ---


def test_fetch_yfinance_price_fast_info():
    import price_fetcher

    tk = mock.MagicMock()
    tk.fast_info.last_price = 150.0
    with mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk):
        assert price_fetcher.fetch_yfinance_price("AAPL") == 150.0


def test_fetch_yfinance_price_history_fallback():
    import pandas as pd

    import price_fetcher

    tk = mock.MagicMock()
    tk.fast_info.last_price = None
    tk.history.return_value = pd.DataFrame({"Close": [145.0]})
    with mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk):
        assert price_fetcher.fetch_yfinance_price("AAPL") == 145.0


def test_fetch_yfinance_price_empty_history():
    import pandas as pd

    import price_fetcher

    tk = mock.MagicMock()
    tk.fast_info.last_price = None
    tk.history.return_value = pd.DataFrame()
    with mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk):
        assert price_fetcher.fetch_yfinance_price("AAPL") is None


def test_fetch_yfinance_price_commodity_mapping():
    import price_fetcher

    tk = mock.MagicMock()
    tk.fast_info.last_price = 30.0
    with mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk) as mock_yf:
        price_fetcher.fetch_yfinance_price("XAG")
    mock_yf.assert_called_with("SI=F")


def test_fetch_yfinance_price_exception():
    import price_fetcher

    with mock.patch.object(price_fetcher.yf, "Ticker", side_effect=Exception("err")):
        assert price_fetcher.fetch_yfinance_price("AAPL") is None


# --- fetch_price ---


def test_fetch_price_commodity():
    import price_fetcher

    tk = mock.MagicMock()
    tk.fast_info.last_price = 30.0
    with mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk):
        price, source = price_fetcher.fetch_price("XAG")
    assert price == 30.0
    assert source == "yfinance"


def test_fetch_price_commodity_failure():
    import price_fetcher

    with mock.patch.object(price_fetcher.yf, "Ticker", side_effect=Exception("fail")):
        price, source = price_fetcher.fetch_price("XAG")
    assert price is None
    assert source == ""


def test_fetch_price_tiingo_success(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    resp = mock.MagicMock()
    resp.json.return_value = [{"last": 150.5}]
    with mock.patch.object(price_fetcher.requests, "get", return_value=resp):
        price, source = price_fetcher.fetch_price("AAPL")
    assert price == 150.5
    assert source == "tiingo"


def test_fetch_price_fallback_yfinance(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    tk = mock.MagicMock()
    tk.fast_info.last_price = 145.0
    with (
        mock.patch.object(price_fetcher.requests, "get", side_effect=Exception("down")),
        mock.patch.object(price_fetcher.yf, "Ticker", return_value=tk),
    ):
        price, source = price_fetcher.fetch_price("AAPL")
    assert price == 145.0
    assert source == "yfinance"


def test_fetch_price_both_fail(monkeypatch):
    import price_fetcher

    monkeypatch.setattr(settings, "tiingo_api_key", "test-key")
    with (
        mock.patch.object(price_fetcher.requests, "get", side_effect=Exception("down")),
        mock.patch.object(price_fetcher.yf, "Ticker", side_effect=Exception("down")),
    ):
        price, source = price_fetcher.fetch_price("AAPL")
    assert price is None
    assert source == ""


# --- _get_holdings_tickers ---


def test_get_holdings_tickers_empty():
    from price_fetcher import _get_holdings_tickers

    assert _get_holdings_tickers() == []


def test_get_holdings_tickers_filters_bonds():
    from price_fetcher import _get_holdings_tickers

    _seed_holding("AAPL", "long_term")
    _seed_holding("BOND1", "bond")
    tickers = _get_holdings_tickers()
    assert "AAPL" in tickers
    assert "BOND1" not in tickers


# --- _get_iwda_holdings_tickers ---


def test_get_iwda_holdings_tickers_empty():
    from price_fetcher import _get_iwda_holdings_tickers

    assert _get_iwda_holdings_tickers() == []


def test_get_iwda_holdings_tickers_returns_latest_snapshot():
    from price_fetcher import _get_iwda_holdings_tickers

    # Seed two snapshots; only the latest should be returned
    _seed_iwda_holding("MSFT", "2024-01-01T00:00:00+00:00", rank=1)
    _seed_iwda_holding("NVDA", "2024-02-01T00:00:00+00:00", rank=1)
    _seed_iwda_holding("AAPL", "2024-02-01T00:00:00+00:00", rank=2)

    tickers = _get_iwda_holdings_tickers()
    assert "NVDA" in tickers
    assert "AAPL" in tickers
    assert "MSFT" not in tickers


# --- _build_ticker_universe ---


def test_build_ticker_universe_includes_iwda_always():
    from price_fetcher import _build_ticker_universe

    # No holdings, no iwda_holdings
    universe = _build_ticker_universe()
    assert universe == ["IWDA.L"]


def test_build_ticker_universe_combines_sources():
    from price_fetcher import _build_ticker_universe

    _seed_holding("AAPL", "long_term")
    _seed_holding("BOND1", "bond")  # bonds excluded
    _seed_iwda_holding("MSFT", "2024-01-01T00:00:00+00:00", rank=1)

    universe = _build_ticker_universe()
    assert "AAPL" in universe
    assert "MSFT" in universe
    assert "IWDA.L" in universe
    assert "BOND1" not in universe
    # Result is sorted
    assert universe == sorted(universe)


def test_build_ticker_universe_deduplicates():
    from price_fetcher import _build_ticker_universe

    # AAPL in both holdings and iwda_holdings
    _seed_holding("AAPL", "long_term")
    _seed_iwda_holding("AAPL", "2024-01-01T00:00:00+00:00", rank=1)

    universe = _build_ticker_universe()
    assert universe.count("AAPL") == 1


def test_build_ticker_universe_empty_iwda_holdings():
    from price_fetcher import _build_ticker_universe

    _seed_holding("TSLA", "long_term")
    universe = _build_ticker_universe()
    assert "TSLA" in universe
    assert "IWDA.L" in universe
    assert universe == sorted(universe)


def test_build_ticker_universe_empty_holdings():
    from price_fetcher import _build_ticker_universe

    _seed_iwda_holding("MSFT", "2024-01-01T00:00:00+00:00", rank=1)
    universe = _build_ticker_universe()
    assert "MSFT" in universe
    assert "IWDA.L" in universe


# --- fetch_price_with_anomaly_check ---


def test_anomaly_check_no_prior_price_skips_check(monkeypatch):
    """No prior price → return Tiingo without any yfinance call."""
    import price_fetcher

    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: 100.0)
    yf_mock = mock.MagicMock()
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", yf_mock)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", None, 1.1)

    assert price == 100.0
    assert source == "tiingo"
    yf_mock.assert_not_called()


def test_anomaly_check_small_change_no_crosscheck(monkeypatch):
    """Move within 5% → no yfinance cross-check."""
    import price_fetcher

    # prior_price_eur = 100.0, current_eurusd = 1.0, tiingo = 102.0 → 2% change
    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: 102.0)
    yf_mock = mock.MagicMock()
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", yf_mock)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", 100.0, 1.0)

    assert price == 102.0
    assert source == "tiingo"
    yf_mock.assert_not_called()


def test_anomaly_check_tiingo_fails_returns_none(monkeypatch):
    """Tiingo fails entirely → return (None, '')."""
    import price_fetcher

    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: None)
    yf_mock = mock.MagicMock()
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", yf_mock)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", 100.0, 1.0)

    assert price is None
    assert source == ""
    yf_mock.assert_not_called()


def test_anomaly_check_large_move_yfinance_agrees(monkeypatch):
    """Tiingo >5% off, yfinance within 2% → Tiingo wins, source='tiingo'."""
    import price_fetcher

    # prior EUR = 100, EURUSD = 1.0
    # tiingo = 110 → 10% change  (triggers cross-check)
    # yfinance = 111 → cross-diff = |110-111|/111 = ~0.9% (within 2%)
    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: 110.0)
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", lambda t: 111.0)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", 100.0, 1.0)

    assert price == 110.0
    assert source == "tiingo"


def test_anomaly_check_large_move_yfinance_disagrees(monkeypatch):
    """Tiingo >5% off, yfinance disagrees by >2% → yfinance wins."""
    import price_fetcher

    # prior EUR = 100, EURUSD = 1.0
    # tiingo = 115 → 15% change  (triggers cross-check)
    # yfinance = 102 → cross-diff = |115-102|/102 = ~12.7% (>2%)
    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: 115.0)
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", lambda t: 102.0)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", 100.0, 1.0)

    assert price == 102.0
    assert source == "yfinance"


def test_anomaly_check_large_move_yfinance_fails(monkeypatch):
    """Tiingo >5% off, yfinance also fails → accept Tiingo with warning."""
    import price_fetcher

    # prior EUR = 100, EURUSD = 1.0, tiingo = 115 → 15% change
    monkeypatch.setattr(price_fetcher, "fetch_tiingo_price", lambda t: 115.0)
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", lambda t: None)

    price, source = price_fetcher.fetch_price_with_anomaly_check("AAPL", 100.0, 1.0)

    assert price == 115.0
    assert source == "tiingo"


# --- fetch_all_prices ---


def test_fetch_all_prices_all_fetch_fail(monkeypatch):
    """All price fetches return None → stored list is empty."""
    import price_fetcher

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (None, ""))
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", lambda t: None)

    # IWDA.L is always in universe; anomaly check returns None, yfinance fallback also None
    result = price_fetcher.fetch_all_prices()
    assert result == []


def test_fetch_all_prices_success(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(
        price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (150.0, "tiingo")
    )
    result = price_fetcher.fetch_all_prices()
    tickers = {pp.ticker for pp in result}
    assert "AAPL" in tickers
    assert "IWDA.L" in tickers
    assert all(pp.close_eur is not None for pp in result)


def test_fetch_all_prices_no_fx(monkeypatch):
    """When get_rate_for_date raises, close_eur is None."""
    import price_fetcher

    _seed_holding("AAPL")
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    # Patch fetch_price so anomaly branch is bypassed (no FX rate cached)
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (150.0, "tiingo"))
    result = price_fetcher.fetch_all_prices()
    # AAPL and IWDA.L should both be attempted; close_eur is None for all
    assert len(result) >= 1
    assert all(pp.close_eur is None for pp in result)


def test_fetch_all_prices_price_none(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (None, ""))
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", lambda t: None)
    assert price_fetcher.fetch_all_prices() == []


def test_fetch_all_prices_ecb_exception(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(
        price_fetcher, "fetch_ecb_rates", mock.MagicMock(side_effect=Exception("ECB down"))
    )
    monkeypatch.setattr(
        price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (150.0, "tiingo")
    )
    result = price_fetcher.fetch_all_prices()
    assert len(result) >= 1


def test_fetch_all_prices_commodity_bypasses_anomaly(monkeypatch):
    """Commodity tickers (XAG) must go through fetch_price, not anomaly check."""
    import price_fetcher

    _seed_holding("XAG")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    # Track which tickers hit the anomaly check vs fetch_price
    anomaly_called_for: list[str] = []

    def _anomaly_check(ticker, prior_price_eur, current_eurusd):
        anomaly_called_for.append(ticker)
        return (150.0, "tiingo")

    monkeypatch.setattr(price_fetcher, "fetch_price_with_anomaly_check", _anomaly_check)
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (30.0, "yfinance"))
    result = price_fetcher.fetch_all_prices()
    xag_results = [pp for pp in result if pp.ticker == "XAG"]
    assert len(xag_results) == 1
    # XAG is a commodity — must NOT have gone through anomaly check
    assert "XAG" not in anomaly_called_for


def test_fetch_all_prices_anomaly_fallback_to_yfinance(monkeypatch):
    """When anomaly check returns (None, ''), yfinance fallback is attempted."""
    import price_fetcher

    _seed_holding("AAPL")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (None, ""))
    yf_mock = mock.MagicMock(return_value=145.0)
    monkeypatch.setattr(price_fetcher, "fetch_yfinance_price", yf_mock)
    result = price_fetcher.fetch_all_prices()
    aapl_results = [pp for pp in result if pp.ticker == "AAPL"]
    assert len(aapl_results) == 1
    assert aapl_results[0].source == "yfinance"


def test_fetch_all_prices_includes_iwda_holdings_tickers(monkeypatch):
    """Tickers from iwda_holdings are included in the fetch universe."""
    import price_fetcher

    _seed_iwda_holding("MSFT", "2024-01-01T00:00:00+00:00", rank=1)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(
        price_fetcher, "fetch_price_with_anomaly_check", lambda t, p, e: (150.0, "tiingo")
    )
    result = price_fetcher.fetch_all_prices()
    tickers = {pp.ticker for pp in result}
    assert "MSFT" in tickers
    assert "IWDA.L" in tickers


def test_fetch_all_prices_prior_price_passed_to_anomaly_check(monkeypatch):
    """Yesterday's EUR price is looked up and passed to the anomaly check."""
    import price_fetcher

    _seed_holding("AAPL")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _seed_price("AAPL", yesterday, 140.0, 127.0)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    captured: list = []

    def _fake_anomaly(ticker, prior_price_eur, current_eurusd):
        captured.append((ticker, prior_price_eur))
        return (150.0, "tiingo")

    monkeypatch.setattr(price_fetcher, "fetch_price_with_anomaly_check", _fake_anomaly)
    price_fetcher.fetch_all_prices()
    aapl_calls = [(t, p) for t, p in captured if t == "AAPL"]
    assert len(aapl_calls) == 1
    assert aapl_calls[0][1] == pytest.approx(127.0)


# --- get_current_price ---


def test_get_current_price_found():
    from price_fetcher import get_current_price

    _seed_price("AAPL", "2024-04-08", 150.0, 136.0)
    pp = get_current_price("AAPL")
    assert pp is not None
    assert pp.close_usd == 150.0


def test_get_current_price_not_found():
    from price_fetcher import get_current_price

    assert get_current_price("NONEXIST") is None


# --- get_price_on_date ---


def test_get_price_on_date_found_date_obj():
    from price_fetcher import get_price_on_date

    _seed_price("AAPL", "2024-04-08", 150.0, 136.0)
    pp = get_price_on_date("AAPL", date(2024, 4, 10))
    assert pp is not None


def test_get_price_on_date_found_string():
    from price_fetcher import get_price_on_date

    _seed_price("AAPL", "2024-04-08", 150.0, 136.0)
    pp = get_price_on_date("AAPL", "2024-04-10")
    assert pp is not None


def test_get_price_on_date_not_found():
    from price_fetcher import get_price_on_date

    assert get_price_on_date("AAPL", "2020-01-01") is None


# --- get_price_change ---


def test_get_price_change_sufficient():
    from price_fetcher import get_price_change

    _seed_price("AAPL", "2024-03-01", None, 100.0)
    _seed_price("AAPL", "2024-04-01", None, 110.0)
    change = get_price_change("AAPL", 30)
    assert change == pytest.approx(10.0)


def test_get_price_change_insufficient():
    from price_fetcher import get_price_change

    assert get_price_change("AAPL", 30) is None


def test_get_price_change_zero_oldest():
    from price_fetcher import get_price_change

    _seed_price("AAPL", "2024-03-01", None, 0.0)
    _seed_price("AAPL", "2024-04-01", None, 110.0)
    assert get_price_change("AAPL", 30) is None


# --- main ---


def test_main_no_prices(monkeypatch, capsys):
    import price_fetcher

    monkeypatch.setattr(price_fetcher, "fetch_all_prices", lambda: [])
    price_fetcher.main()
    assert "No prices fetched" in capsys.readouterr().out


def test_main_with_prices(monkeypatch, capsys):
    import price_fetcher

    points = [
        PricePoint(
            ticker="AAPL", date=date(2024, 4, 8), close_usd=150.0, close_eur=136.0, source="tiingo"
        ),
        PricePoint(
            ticker="MSFT", date=date(2024, 4, 8), close_usd=None, close_eur=None, source="yfinance"
        ),
    ]
    monkeypatch.setattr(price_fetcher, "fetch_all_prices", lambda: points)
    price_fetcher.main()
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "MSFT" in out
