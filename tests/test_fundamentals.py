"""Unit tests for fundamentals.py."""

from datetime import date, datetime
from unittest import mock

from db import db_conn


def _seed_holding(ticker: str, pool: str = "long_term") -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, 10.0, 100.0, 1.1, "2024-01-01", pool),
        )


# --- fetch_fundamentals ---


def test_fetch_fundamentals_success():
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {
        "trailingPE": 25.5,
        "priceToSalesTrailing12Months": 8.0,
        "revenueGrowth": 0.15,
        "profitMargins": 0.25,
        "freeCashflow": 1e10,
        "debtToEquity": 150.0,
        "dividendYield": 0.005,
        "marketCap": 3e12,
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "United States",
    }
    tk.calendar = None
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.ticker == "AAPL"
    assert fund.pe_ratio == 25.5
    assert fund.market_cap == 3e12


def test_fetch_fundamentals_exception():
    import fundamentals

    with mock.patch.object(fundamentals.yf, "Ticker", side_effect=Exception("err")):
        assert fundamentals.fetch_fundamentals("AAPL") is None


def test_fetch_fundamentals_empty_info():
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {}
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        assert fundamentals.fetch_fundamentals("AAPL") is None


def test_fetch_fundamentals_none_equity():
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {"quoteType": "NONE_EQUITY"}
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        assert fundamentals.fetch_fundamentals("AAPL") is None


def test_fetch_fundamentals_calendar_datetime():
    """Calendar with datetime-like object that has .date()."""
    import fundamentals

    mock_dt = mock.MagicMock()
    mock_dt.date.return_value = date(2024, 7, 25)

    tk = mock.MagicMock()
    tk.info = {"trailingPE": 20.0}
    tk.calendar = {"Earnings Date": [mock_dt]}
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.next_earnings == date(2024, 7, 25)


def test_fetch_fundamentals_calendar_string():
    """Calendar with earnings date as ISO string."""
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {"trailingPE": 20.0}
    tk.calendar = {"Earnings Date": ["2024-07-25T00:00:00"]}
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.next_earnings == date(2024, 7, 25)


def test_fetch_fundamentals_calendar_not_dict():
    """Calendar is not a dict — skip parsing."""
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {"trailingPE": 20.0}
    tk.calendar = "not-a-dict"
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.next_earnings is None


def test_fetch_fundamentals_calendar_empty_earnings():
    """Calendar dict with empty Earnings Date list."""
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {"trailingPE": 20.0}
    tk.calendar = {"Earnings Date": []}
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.next_earnings is None


def test_fetch_fundamentals_calendar_exception():
    """Calendar property raises — caught and skipped."""
    import fundamentals

    tk = mock.MagicMock()
    tk.info = {"trailingPE": 20.0}
    type(tk).calendar = mock.PropertyMock(side_effect=Exception("calendar err"))
    with mock.patch.object(fundamentals.yf, "Ticker", return_value=tk):
        fund = fundamentals.fetch_fundamentals("AAPL")
    assert fund is not None
    assert fund.next_earnings is None


# --- _fmt_cap ---


def test_fmt_cap():
    from fundamentals import _fmt_cap

    assert _fmt_cap(None) == "—"
    assert _fmt_cap(3e12) == "$3.0T"
    assert _fmt_cap(1.5e9) == "$1.5B"
    assert _fmt_cap(500e6) == "$500M"
    assert _fmt_cap(50000) == "$50,000"


# --- _get_stock_tickers ---


def test_get_stock_tickers_empty():
    from fundamentals import _get_stock_tickers

    assert _get_stock_tickers() == []


def test_get_stock_tickers_filters():
    from fundamentals import _get_stock_tickers

    _seed_holding("AAPL", "long_term")
    _seed_holding("XAG", "short_term")  # commodity → now included
    _seed_holding("BOND1", "bond")  # bond pool → excluded
    tickers = _get_stock_tickers()
    assert "AAPL" in tickers
    assert "XAG" in tickers
    assert "BOND1" not in tickers


# --- fetch_all_fundamentals ---


def test_fetch_all_fundamentals_no_holdings():
    from fundamentals import fetch_all_fundamentals

    assert fetch_all_fundamentals() == []


def test_fetch_all_fundamentals_with_data(monkeypatch):
    import fundamentals as fund_mod
    from fundamentals import Fundamentals

    _seed_holding("AAPL")
    mock_fund = Fundamentals(ticker="AAPL", fetched_at=datetime.now(), pe_ratio=25.0)
    monkeypatch.setattr(fund_mod, "fetch_fundamentals", lambda t: mock_fund)
    result = fund_mod.fetch_all_fundamentals()
    assert len(result) == 1


def test_fetch_all_fundamentals_partial_failure(monkeypatch):
    import fundamentals as fund_mod
    from fundamentals import Fundamentals

    _seed_holding("AAPL")
    _seed_holding("FAIL", "short_term")

    def mock_fetch(ticker):
        if ticker == "AAPL":
            return Fundamentals(ticker="AAPL", fetched_at=datetime.now())
        return None

    monkeypatch.setattr(fund_mod, "fetch_fundamentals", mock_fetch)
    result = fund_mod.fetch_all_fundamentals()
    assert len(result) == 1


# --- get_latest_fundamentals ---


def test_get_latest_fundamentals_not_found():
    from fundamentals import get_latest_fundamentals

    assert get_latest_fundamentals("NONEXIST") is None


def test_get_latest_fundamentals_found():
    from fundamentals import get_latest_fundamentals

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fundamentals (ticker, fetched_at, pe_ratio, sector, raw_json)"
            " VALUES (?, ?, ?, ?, ?)",
            ("AAPL", "2024-04-08T10:00:00", 25.0, "Tech", '{"k":"v"}'),
        )
    result = get_latest_fundamentals("AAPL")
    assert result is not None
    assert result.pe_ratio == 25.0


# --- main ---


def test_main_no_results(monkeypatch, capsys):
    import fundamentals as fund_mod

    monkeypatch.setattr(fund_mod, "fetch_all_fundamentals", lambda: [])
    fund_mod.main()
    assert "No fundamentals fetched" in capsys.readouterr().out


def test_main_with_results(monkeypatch, capsys):
    import fundamentals as fund_mod
    from fundamentals import Fundamentals

    results = [
        Fundamentals(
            ticker="AAPL",
            fetched_at=datetime.now(),
            pe_ratio=25.5,
            ps_ratio=8.0,
            revenue_growth=0.15,
            profit_margin=0.25,
            debt_to_equity=150.0,
            market_cap=3e12,
            sector="Technology",
        ),
        Fundamentals(ticker="GOOG", fetched_at=datetime.now()),
    ]
    monkeypatch.setattr(fund_mod, "fetch_all_fundamentals", lambda: results)
    fund_mod.main()
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "GOOG" in out
