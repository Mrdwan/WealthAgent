"""Unit tests for price_fetcher.py."""

from datetime import date
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


# --- fetch_all_prices ---


def test_fetch_all_prices_no_holdings():
    from price_fetcher import fetch_all_prices

    assert fetch_all_prices() == []


def test_fetch_all_prices_success(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            (date.today().isoformat(), "EURUSD", 1.1),
        )
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (150.0, "tiingo"))
    result = price_fetcher.fetch_all_prices()
    assert len(result) == 1
    assert result[0].close_eur is not None


def test_fetch_all_prices_no_fx(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (150.0, "tiingo"))
    result = price_fetcher.fetch_all_prices()
    assert len(result) == 1
    assert result[0].close_eur is None


def test_fetch_all_prices_price_none(monkeypatch):
    import price_fetcher

    _seed_holding("AAPL")
    monkeypatch.setattr(price_fetcher, "fetch_ecb_rates", lambda: [])
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (None, ""))
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
    monkeypatch.setattr(price_fetcher, "fetch_price", lambda t: (150.0, "tiingo"))
    result = price_fetcher.fetch_all_prices()
    assert len(result) == 1


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
