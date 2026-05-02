"""Unit tests for iwda_fetcher.py."""

from datetime import UTC, datetime
from unittest import mock

import pytest
import requests

from config.settings import settings
from db import IwdaHolding, db_conn

# ---------------------------------------------------------------------------
# Canned HTML / CSV fixtures
# ---------------------------------------------------------------------------

_ISHARES_CSV_VALID = (
    "iShares MSCI World UCITS ETF (Acc)\n"
    "Fund Holdings as of 30-Apr-2025\n"
    "Inception Date,25-Sep-2009\n"
    'Shares Outstanding,"123,456,789"\n'
    "Stock Exchange,London Stock Exchange\n"
    "Products,1\n"
    "\n"
    "Ticker,Name,Asset Class,Market Value,Weight (%),Notional Value,"
    "Shares,Price,Location,Exchange,Currency,FX Rate,Market Currency,Accrual Date\n"
    'AAPL,"Apple Inc",Equity,"10,000",5.50,,"100",100.00,United States,NASDAQ,USD,1.0,USD,\n'
    'MSFT,"Microsoft Corp",Equity,"9,000",4.90,,"90",100.00,United States,NASDAQ,USD,1.0,USD,\n'
    'NVDA,"NVIDIA Corp",Equity,"8,000",4.20,,"80",100.00,United States,NASDAQ,USD,1.0,USD,\n'
    'AMZN,"Amazon.com Inc",Equity,"7,500",3.80,,"75",100.00,United States,NASDAQ,USD,1.0,USD,\n'
    'GOOGL,"Alphabet Inc Class A",Equity,"7,000",3.50,,"70",100.00,'
    "United States,NASDAQ,USD,1.0,USD,\n"
    'GOOG,"Alphabet Inc Class C",Equity,"6,500",3.20,,"65",100.00,'
    "United States,NASDAQ,USD,1.0,USD,\n"
    'META,"Meta Platforms",Equity,"6,000",2.90,,"60",100.00,United States,NASDAQ,USD,1.0,USD,\n'
    "-,Cash and/or Derivatives,Cash,,0.10,,,,,,,,,,\n"
)

_ISHARES_CSV_NO_HEADER = """\
iShares MSCI World UCITS ETF
Some preamble line
Another line
"""

_ISHARES_CSV_EMPTY_DATA = """\
iShares MSCI World UCITS ETF
Ticker,Name,Asset Class,Weight (%)
"""

_JUSTETF_HTML_VALID = """\
<html><body>
<table>
  <thead>
    <tr>
      <th>Symbol</th>
      <th>Name</th>
      <th>Weight</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>AAPL</td>
      <td>Apple Inc</td>
      <td>5.50%</td>
    </tr>
    <tr>
      <td>MSFT</td>
      <td>Microsoft Corp</td>
      <td>4.90%</td>
    </tr>
    <tr>
      <td>NVDA</td>
      <td>NVIDIA Corp</td>
      <td>4.20%</td>
    </tr>
    <tr>
      <td>-</td>
      <td>Cash</td>
      <td>0.10%</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

_JUSTETF_HTML_NO_TABLE = "<html><body><p>No table here</p></body></html>"

_JUSTETF_HTML_NO_WEIGHT_COL = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th></tr></thead>
  <tbody><tr><td>AAPL</td><td>Apple</td></tr></tbody>
</table>
</body></html>
"""

_STOCKANALYSIS_HTML_VALID = """\
<html><body>
<table>
  <thead>
    <tr>
      <th>Symbol</th>
      <th>Name</th>
      <th>% Weight</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>AAPL</td>
      <td>Apple Inc</td>
      <td>5.50%</td>
    </tr>
    <tr>
      <td>MSFT</td>
      <td>Microsoft Corp</td>
      <td>4.90%</td>
    </tr>
    <tr>
      <td>NVDA</td>
      <td>NVIDIA Corp</td>
      <td>4.20%</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

_STOCKANALYSIS_HTML_NO_TABLE = "<html><body><p>Nothing here</p></body></html>"

_STOCKANALYSIS_HTML_NO_WEIGHT = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th></tr></thead>
  <tbody><tr><td>AAPL</td><td>Apple</td></tr></tbody>
</table>
</body></html>
"""

# HTML where ticker cells are empty (to test skip logic)
_STOCKANALYSIS_HTML_EMPTY_TICKER = """\
<html><body>
<table>
  <thead>
    <tr><th>Symbol</th><th>Name</th><th>% Weight</th></tr>
  </thead>
  <tbody>
    <tr><td></td><td>Cash</td><td>0.10%</td></tr>
    <tr><td>AAPL</td><td>Apple</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_holding(ticker: str, weight: float, rank: int, fetched_at: datetime) -> IwdaHolding:
    return IwdaHolding(
        ticker=ticker,
        name=f"{ticker} Inc",
        weight_pct=weight,
        rank=rank,
        fetched_at=fetched_at,
    )


def _mock_resp(text: str, status: int = 200) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.text = text
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _seed_snapshot(tickers: list[str], fetched_at: datetime, base_rank: int = 1) -> None:
    """Insert a holdings snapshot directly into the DB for test setup."""
    with db_conn() as conn:
        for i, ticker in enumerate(tickers):
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (ticker, f"{ticker} Inc", 5.0 - i * 0.1, base_rank + i, fetched_at.isoformat()),
            )


# ---------------------------------------------------------------------------
# _td_text helper tests
# ---------------------------------------------------------------------------


class TestTdText:
    def test_returns_empty_when_idx_is_none(self):
        """idx=None returns empty string (covers the idx is None branch)."""
        from iwda_fetcher import _td_text

        assert _td_text([], None) == ""

    def test_returns_empty_when_idx_out_of_range(self):
        from iwda_fetcher import _td_text

        assert _td_text([], 0) == ""

    def test_returns_text_when_in_range(self):
        from unittest.mock import MagicMock

        from iwda_fetcher import _td_text

        cell = MagicMock()
        cell.get_text.return_value = "AAPL"
        assert _td_text([cell], 0) == "AAPL"


# ---------------------------------------------------------------------------
# Parser tests — pure functions, no HTTP
# ---------------------------------------------------------------------------


class TestParseIsharesCSV:
    def test_valid_csv_parses_holdings(self):
        from iwda_fetcher import _parse_ishares_csv

        holdings = _parse_ishares_csv(_ISHARES_CSV_VALID)
        tickers = [h.ticker for h in holdings]
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        # Dash rows (cash) should be skipped
        assert "-" not in tickers

    def test_ranks_are_sequential(self):
        from iwda_fetcher import _parse_ishares_csv

        holdings = _parse_ishares_csv(_ISHARES_CSV_VALID)
        assert [h.rank for h in holdings] == list(range(1, len(holdings) + 1))

    def test_weight_parsed_correctly(self):
        from iwda_fetcher import _parse_ishares_csv

        holdings = _parse_ishares_csv(_ISHARES_CSV_VALID)
        aapl = next(h for h in holdings if h.ticker == "AAPL")
        assert aapl.weight_pct == pytest.approx(5.50)

    def test_no_header_returns_empty(self):
        from iwda_fetcher import _parse_ishares_csv

        assert _parse_ishares_csv(_ISHARES_CSV_NO_HEADER) == []

    def test_header_with_no_data_rows_returns_empty(self):
        from iwda_fetcher import _parse_ishares_csv

        assert _parse_ishares_csv(_ISHARES_CSV_EMPTY_DATA) == []

    def test_empty_string_returns_empty(self):
        from iwda_fetcher import _parse_ishares_csv

        assert _parse_ishares_csv("") == []

    def test_non_numeric_weight_row_skipped(self):
        from iwda_fetcher import _parse_ishares_csv

        csv_text = (
            "Ticker,Name,Asset Class,Weight (%)\n"
            "AAPL,Apple Inc,Equity,N/A\n"
            "MSFT,Microsoft,Equity,4.90\n"
        )
        holdings = _parse_ishares_csv(csv_text)
        assert len(holdings) == 1
        assert holdings[0].ticker == "MSFT"

    def test_missing_name_column_returns_empty(self):
        """Header present but missing 'Name' column → ValueError branch → []."""
        from iwda_fetcher import _parse_ishares_csv

        # Has Ticker and Weight (%) but no Name column
        csv_text = "Ticker,Asset Class,Weight (%)\nAAPL,Equity,5.50\n"
        assert _parse_ishares_csv(csv_text) == []

    def test_short_row_padded(self):
        """Rows shorter than required columns are padded with empty strings."""
        from iwda_fetcher import _parse_ishares_csv

        # Header has 4 cols; data row only has ticker (truncated)
        csv_text = "Ticker,Name,Asset Class,Weight (%)\nAAPL\n"
        holdings = _parse_ishares_csv(csv_text)
        # Weight field is empty → float("") raises → row skipped
        assert holdings == []


class TestParseJustetfHTML:
    def test_valid_html_parses_holdings(self):
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(_JUSTETF_HTML_VALID)
        tickers = [h.ticker for h in holdings]
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        # Dash rows skipped
        assert "-" not in tickers

    def test_ranks_are_sequential(self):
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(_JUSTETF_HTML_VALID)
        assert [h.rank for h in holdings] == list(range(1, len(holdings) + 1))

    def test_no_table_returns_empty(self):
        from iwda_fetcher import _parse_justetf_html

        assert _parse_justetf_html(_JUSTETF_HTML_NO_TABLE) == []

    def test_no_weight_column_returns_empty(self):
        from iwda_fetcher import _parse_justetf_html

        assert _parse_justetf_html(_JUSTETF_HTML_NO_WEIGHT_COL) == []

    def test_weight_stripped_of_percent(self):
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(_JUSTETF_HTML_VALID)
        aapl = next(h for h in holdings if h.ticker == "AAPL")
        assert aapl.weight_pct == pytest.approx(5.50)

    def test_missing_name_column_uses_ticker(self):
        """When name_idx is None, name defaults to ticker."""
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Weight</th></tr></thead>
  <tbody>
    <tr><td>AAPL</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(html)
        assert holdings[0].ticker == "AAPL"
        assert holdings[0].name == "AAPL"

    def test_non_numeric_weight_row_skipped(self):
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th><th>Weight</th></tr></thead>
  <tbody>
    <tr><td>AAPL</td><td>Apple</td><td>N/A</td></tr>
    <tr><td>MSFT</td><td>Microsoft</td><td>4.90%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(html)
        assert len(holdings) == 1
        assert holdings[0].ticker == "MSFT"

    def test_empty_ticker_rows_skipped(self):
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th><th>Weight</th></tr></thead>
  <tbody>
    <tr><td></td><td>Cash</td><td>0.10%</td></tr>
    <tr><td>AAPL</td><td>Apple</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(html)
        assert len(holdings) == 1
        assert holdings[0].ticker == "AAPL"

    def test_empty_tr_rows_skipped(self):
        """Rows with no <td> elements (e.g. header rows in tbody) are skipped."""
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th><th>Weight</th></tr></thead>
  <tbody>
    <tr></tr>
    <tr><td>AAPL</td><td>Apple</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_justetf_html

        holdings = _parse_justetf_html(html)
        assert len(holdings) == 1

    def test_unrecognised_header_columns_ignored(self):
        """Headers that match neither symbol/name/weight leave those indices None."""
        # "Weight" is present so the table is found; other columns are unrecognised
        html = """\
<html><body>
<table>
  <thead><tr><th>ISIN</th><th>Sector</th><th>Weight</th></tr></thead>
  <tbody>
    <tr><td>IE000X</td><td>Tech</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_justetf_html

        # ticker_idx is None → ticker == "" → row skipped → empty list
        holdings = _parse_justetf_html(html)
        assert holdings == []


class TestParseStockanalysisHTML:
    def test_valid_html_parses_holdings(self):
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(_STOCKANALYSIS_HTML_VALID)
        tickers = [h.ticker for h in holdings]
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "NVDA" in tickers

    def test_no_table_returns_empty(self):
        from iwda_fetcher import _parse_stockanalysis_html

        assert _parse_stockanalysis_html(_STOCKANALYSIS_HTML_NO_TABLE) == []

    def test_no_weight_column_returns_empty(self):
        from iwda_fetcher import _parse_stockanalysis_html

        assert _parse_stockanalysis_html(_STOCKANALYSIS_HTML_NO_WEIGHT) == []

    def test_empty_ticker_skipped(self):
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(_STOCKANALYSIS_HTML_EMPTY_TICKER)
        assert len(holdings) == 1
        assert holdings[0].ticker == "AAPL"

    def test_missing_name_column_uses_ticker(self):
        """When name_idx is None, name defaults to ticker."""
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>% Weight</th></tr></thead>
  <tbody><tr><td>AAPL</td><td>5.50%</td></tr></tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(html)
        assert holdings[0].name == "AAPL"

    def test_ranks_sequential(self):
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(_STOCKANALYSIS_HTML_VALID)
        assert [h.rank for h in holdings] == list(range(1, len(holdings) + 1))

    def test_non_numeric_weight_row_skipped(self):
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th><th>% Weight</th></tr></thead>
  <tbody>
    <tr><td>AAPL</td><td>Apple</td><td>N/A</td></tr>
    <tr><td>MSFT</td><td>Microsoft</td><td>4.90%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(html)
        assert len(holdings) == 1
        assert holdings[0].ticker == "MSFT"

    def test_empty_tr_rows_skipped(self):
        """Rows with no <td> elements are skipped (covers the `continue` branch)."""
        html = """\
<html><body>
<table>
  <thead><tr><th>Symbol</th><th>Name</th><th>% Weight</th></tr></thead>
  <tbody>
    <tr></tr>
    <tr><td>AAPL</td><td>Apple</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_stockanalysis_html

        holdings = _parse_stockanalysis_html(html)
        assert len(holdings) == 1

    def test_unrecognised_header_columns_ignored(self):
        """Header columns that don't match symbol/name lead to None ticker_idx."""
        # "weight" is present (via "% Weight") but no symbol/ticker header col
        html = """\
<html><body>
<table>
  <thead><tr><th>ISIN</th><th>Sector</th><th>% Weight</th></tr></thead>
  <tbody>
    <tr><td>IE000X</td><td>Tech</td><td>5.50%</td></tr>
  </tbody>
</table>
</body></html>
"""
        from iwda_fetcher import _parse_stockanalysis_html

        # ticker_idx is None → ticker == "" → row skipped
        holdings = _parse_stockanalysis_html(html)
        assert holdings == []


# ---------------------------------------------------------------------------
# _http_get tests
# ---------------------------------------------------------------------------


class TestHttpGet:
    def test_http_get_passes_user_agent(self, monkeypatch):
        """_http_get calls requests.get with the expected User-Agent header."""
        import iwda_fetcher

        mock_resp = mock.MagicMock()
        captured: list[dict] = []

        def fake_get(url, headers, timeout):
            captured.append({"url": url, "headers": headers, "timeout": timeout})
            return mock_resp

        monkeypatch.setattr(iwda_fetcher.requests, "get", fake_get)
        iwda_fetcher._http_get("https://example.com/test")

        assert len(captured) == 1
        assert "User-Agent" in captured[0]["headers"]
        assert captured[0]["timeout"] == iwda_fetcher._REQUEST_TIMEOUT


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_primary_succeeds_no_alert(self, monkeypatch):
        """Primary success → no Telegram message sent."""
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher,
            "_fetch_ishares",
            lambda: [_make_holding("AAPL", 5.5, 1, datetime.now(UTC))],
        )
        sent: list[str] = []
        monkeypatch.setattr(iwda_fetcher, "send_message", sent.append)

        holdings = iwda_fetcher.fetch_iwda_holdings()
        assert holdings[0].ticker == "AAPL"
        assert sent == []

    def test_primary_fails_fallback1_used_alert_sent(self, monkeypatch):
        """Primary fails → fallback 1 used → alert sent with source names."""
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_fetch_ishares", mock.Mock(side_effect=RuntimeError("CSV fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher,
            "_fetch_justetf",
            lambda: [_make_holding("MSFT", 4.9, 1, datetime.now(UTC))],
        )
        sent: list[str] = []
        monkeypatch.setattr(iwda_fetcher, "send_message", sent.append)

        holdings = iwda_fetcher.fetch_iwda_holdings()
        assert holdings[0].ticker == "MSFT"
        assert len(sent) == 1
        assert "iShares CSV" in sent[0]
        assert "justETF" in sent[0]
        assert "⚠️" in sent[0]

    def test_primary_and_fallback1_fail_fallback2_used_alert_sent(self, monkeypatch):
        """Primary and fallback 1 fail → fallback 2 used → alert with fallback2 name."""
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_fetch_ishares", mock.Mock(side_effect=RuntimeError("CSV fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher, "_fetch_justetf", mock.Mock(side_effect=RuntimeError("HTML fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher,
            "_fetch_stockanalysis",
            lambda: [_make_holding("NVDA", 4.2, 1, datetime.now(UTC))],
        )
        sent: list[str] = []
        monkeypatch.setattr(iwda_fetcher, "send_message", sent.append)

        holdings = iwda_fetcher.fetch_iwda_holdings()
        assert holdings[0].ticker == "NVDA"
        assert len(sent) == 1
        assert "stockanalysis.com" in sent[0]

    def test_all_sources_fail_cache_available_returns_cache(self, monkeypatch):
        """All live sources fail → cached snapshot returned → alert sent."""
        import iwda_fetcher

        ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        _seed_snapshot(["AAPL", "MSFT"], ts)

        monkeypatch.setattr(
            iwda_fetcher, "_fetch_ishares", mock.Mock(side_effect=RuntimeError("fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher, "_fetch_justetf", mock.Mock(side_effect=RuntimeError("fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher, "_fetch_stockanalysis", mock.Mock(side_effect=RuntimeError("fail"))
        )
        sent: list[str] = []
        monkeypatch.setattr(iwda_fetcher, "send_message", sent.append)

        holdings = iwda_fetcher.fetch_iwda_holdings()
        tickers = [h.ticker for h in holdings]
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert len(sent) == 1
        assert "❌" in sent[0]
        assert "cached snapshot" in sent[0]

    def test_all_sources_fail_no_cache_raises(self, monkeypatch):
        """All sources fail and no cache → exception raised + alert sent."""
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_fetch_ishares", mock.Mock(side_effect=RuntimeError("fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher, "_fetch_justetf", mock.Mock(side_effect=RuntimeError("fail"))
        )
        monkeypatch.setattr(
            iwda_fetcher, "_fetch_stockanalysis", mock.Mock(side_effect=RuntimeError("fail"))
        )
        sent: list[str] = []
        monkeypatch.setattr(iwda_fetcher, "send_message", sent.append)

        with pytest.raises(RuntimeError, match="All IWDA sources failed"):
            iwda_fetcher.fetch_iwda_holdings()

        assert len(sent) == 1
        assert "no cached data available" in sent[0]


# ---------------------------------------------------------------------------
# HTTP helpers tests
# ---------------------------------------------------------------------------


class TestPrivateFetchers:
    def test_fetch_ishares_success(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(iwda_fetcher, "_http_get", lambda url: _mock_resp(_ISHARES_CSV_VALID))
        holdings = iwda_fetcher._fetch_ishares()
        assert any(h.ticker == "AAPL" for h in holdings)

    def test_fetch_ishares_http_error(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(iwda_fetcher, "_http_get", lambda url: _mock_resp("", status=403))
        with pytest.raises(requests.HTTPError):
            iwda_fetcher._fetch_ishares()

    def test_fetch_ishares_empty_parse_raises(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_http_get", lambda url: _mock_resp(_ISHARES_CSV_NO_HEADER)
        )
        with pytest.raises(RuntimeError, match="no parseable holdings"):
            iwda_fetcher._fetch_ishares()

    def test_fetch_justetf_success(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(iwda_fetcher, "_http_get", lambda url: _mock_resp(_JUSTETF_HTML_VALID))
        holdings = iwda_fetcher._fetch_justetf()
        assert any(h.ticker == "AAPL" for h in holdings)

    def test_fetch_justetf_http_error(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(iwda_fetcher, "_http_get", lambda url: _mock_resp("", status=403))
        with pytest.raises(requests.HTTPError):
            iwda_fetcher._fetch_justetf()

    def test_fetch_justetf_empty_parse_raises(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_http_get", lambda url: _mock_resp(_JUSTETF_HTML_NO_TABLE)
        )
        with pytest.raises(RuntimeError, match="no parseable holdings"):
            iwda_fetcher._fetch_justetf()

    def test_fetch_stockanalysis_success(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_http_get", lambda url: _mock_resp(_STOCKANALYSIS_HTML_VALID)
        )
        holdings = iwda_fetcher._fetch_stockanalysis()
        assert any(h.ticker == "AAPL" for h in holdings)

    def test_fetch_stockanalysis_http_error(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(iwda_fetcher, "_http_get", lambda url: _mock_resp("", status=500))
        with pytest.raises(requests.HTTPError):
            iwda_fetcher._fetch_stockanalysis()

    def test_fetch_stockanalysis_empty_parse_raises(self, monkeypatch):
        import iwda_fetcher

        monkeypatch.setattr(
            iwda_fetcher, "_http_get", lambda url: _mock_resp(_STOCKANALYSIS_HTML_NO_TABLE)
        )
        with pytest.raises(RuntimeError, match="no parseable holdings"):
            iwda_fetcher._fetch_stockanalysis()


# ---------------------------------------------------------------------------
# save_holdings / round-trip tests
# ---------------------------------------------------------------------------


class TestSaveHoldings:
    def test_saves_and_reads_back(self):
        from iwda_fetcher import save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts),
            IwdaHolding(
                ticker="MSFT", name="Microsoft Corp", weight_pct=4.9, rank=2, fetched_at=ts
            ),
        ]
        save_holdings(holdings, fetched_at=ts)

        conn = __import__("db").get_conn()
        rows = conn.execute("SELECT * FROM iwda_holdings ORDER BY rank").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0]["ticker"] == "AAPL"
        assert rows[1]["ticker"] == "MSFT"

    def test_idempotent_resave(self):
        """Re-saving the same snapshot is a no-op (INSERT OR IGNORE)."""
        from iwda_fetcher import save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts)
        ]
        save_holdings(holdings, fetched_at=ts)
        save_holdings(holdings, fetched_at=ts)

        conn = __import__("db").get_conn()
        count = conn.execute("SELECT COUNT(*) FROM iwda_holdings").fetchone()[0]
        conn.close()
        assert count == 1

    def test_empty_list_is_noop(self):
        from iwda_fetcher import save_holdings

        save_holdings([])  # should not raise

        conn = __import__("db").get_conn()
        count = conn.execute("SELECT COUNT(*) FROM iwda_holdings").fetchone()[0]
        conn.close()
        assert count == 0

    def test_default_fetched_at_used_when_none(self):
        """When fetched_at is None, current UTC time is used."""
        from iwda_fetcher import save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts)
        ]
        save_holdings(holdings, fetched_at=None)  # should use datetime.now(UTC)

        conn = __import__("db").get_conn()
        count = conn.execute("SELECT COUNT(*) FROM iwda_holdings").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# Alphabet dual-class consolidation tests
# ---------------------------------------------------------------------------


class TestGetConsolidatedTopN:
    def test_googl_goog_merged(self, monkeypatch):
        """GOOGL + GOOG are merged: weights summed, rank = min, ticker = GOOGL."""
        from iwda_fetcher import get_consolidated_top_n, save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts),
            IwdaHolding(
                ticker="GOOGL", name="Alphabet Inc Class A", weight_pct=3.5, rank=2, fetched_at=ts
            ),
            IwdaHolding(
                ticker="GOOG", name="Alphabet Inc Class C", weight_pct=3.2, rank=3, fetched_at=ts
            ),
            IwdaHolding(
                ticker="MSFT", name="Microsoft Corp", weight_pct=4.9, rank=4, fetched_at=ts
            ),
        ]
        save_holdings(holdings, fetched_at=ts)
        monkeypatch.setattr(settings, "iwda_top_n", 10)

        result = get_consolidated_top_n()
        tickers = [h.ticker for h in result]

        # Only GOOGL should appear, not GOOG
        assert "GOOGL" in tickers
        assert "GOOG" not in tickers

        googl = next(h for h in result if h.ticker == "GOOGL")
        assert googl.weight_pct == pytest.approx(3.5 + 3.2)
        assert googl.rank == 2  # min(2, 3)
        assert googl.name == "Alphabet Inc"

    def test_goog_seen_before_googl_merges_correctly(self, monkeypatch):
        """When GOOG rows appear before GOOGL in the DB, merge still works."""
        from iwda_fetcher import get_consolidated_top_n, save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        # Insert GOOG before GOOGL by rank ordering
        holdings = [
            IwdaHolding(
                ticker="GOOG", name="Alphabet Inc Class C", weight_pct=3.2, rank=2, fetched_at=ts
            ),
            IwdaHolding(
                ticker="GOOGL", name="Alphabet Inc Class A", weight_pct=3.5, rank=3, fetched_at=ts
            ),
            IwdaHolding(
                ticker="MSFT", name="Microsoft Corp", weight_pct=4.9, rank=1, fetched_at=ts
            ),
        ]
        save_holdings(holdings, fetched_at=ts)
        monkeypatch.setattr(settings, "iwda_top_n", 10)

        result = get_consolidated_top_n()
        tickers = [h.ticker for h in result]

        assert "GOOGL" in tickers
        assert "GOOG" not in tickers

        googl = next(h for h in result if h.ticker == "GOOGL")
        assert googl.weight_pct == pytest.approx(3.5 + 3.2)
        assert googl.rank == 2  # min(2, 3)

    def test_only_googl_no_goog_still_works(self, monkeypatch):
        """If only GOOGL is present (no GOOG), it is returned normally."""
        from iwda_fetcher import get_consolidated_top_n, save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts),
            IwdaHolding(
                ticker="GOOGL", name="Alphabet Inc Class A", weight_pct=3.5, rank=2, fetched_at=ts
            ),
        ]
        save_holdings(holdings, fetched_at=ts)
        monkeypatch.setattr(settings, "iwda_top_n", 10)

        result = get_consolidated_top_n()
        googl = next(h for h in result if h.ticker == "GOOGL")
        assert googl.weight_pct == pytest.approx(3.5)
        assert googl.name == "Alphabet Inc"

    def test_top_n_truncation(self, monkeypatch):
        """get_consolidated_top_n respects the n parameter."""
        from iwda_fetcher import get_consolidated_top_n, save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(
                ticker=f"T{i:02d}",
                name=f"Company {i}",
                weight_pct=5.0 - i * 0.1,
                rank=i + 1,
                fetched_at=ts,
            )
            for i in range(20)
        ]
        save_holdings(holdings, fetched_at=ts)

        result = get_consolidated_top_n(n=5)
        assert len(result) == 5

    def test_uses_settings_default_n(self, monkeypatch):
        """When n is None, settings.iwda_top_n is used."""
        from iwda_fetcher import get_consolidated_top_n, save_holdings

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        holdings = [
            IwdaHolding(
                ticker=f"T{i:02d}",
                name=f"Co {i}",
                weight_pct=5.0 - i * 0.1,
                rank=i + 1,
                fetched_at=ts,
            )
            for i in range(20)
        ]
        save_holdings(holdings, fetched_at=ts)
        monkeypatch.setattr(settings, "iwda_top_n", 7)

        result = get_consolidated_top_n()
        assert len(result) == 7

    def test_empty_db_returns_empty(self):
        from iwda_fetcher import get_consolidated_top_n

        assert get_consolidated_top_n() == []


# ---------------------------------------------------------------------------
# compute_changes hysteresis tests
# ---------------------------------------------------------------------------


class TestComputeChanges:
    def _setup_two_snapshots(
        self,
        prior_tickers: list[str],
        current_tickers: list[str],
        monkeypatch,
        top_n: int = 15,
        buffer: int = 5,
    ) -> None:
        """Seed two snapshots and configure settings."""
        monkeypatch.setattr(settings, "iwda_top_n", top_n)
        monkeypatch.setattr(settings, "iwda_exit_buffer", buffer)

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        _seed_snapshot(prior_tickers, ts1)
        _seed_snapshot(current_tickers, ts2)

    def test_new_ticker_detected(self, monkeypatch):
        from iwda_fetcher import compute_changes

        prior = [f"T{i:02d}" for i in range(15)]
        current = [f"T{i:02d}" for i in range(14)] + ["NEW"]
        self._setup_two_snapshots(prior, current, monkeypatch)

        changes = compute_changes()
        new_tickers = [item["ticker"] for item in changes["new"]]
        exited_tickers = [item["ticker"] for item in changes["exited"]]
        assert "NEW" in new_tickers
        assert "T14" in exited_tickers

    def test_kept_tickers_reported(self, monkeypatch):
        from iwda_fetcher import compute_changes

        tickers = [f"T{i:02d}" for i in range(15)]
        self._setup_two_snapshots(tickers, tickers, monkeypatch)

        changes = compute_changes()
        assert len(changes["kept"]) == 15
        assert changes["new"] == []
        assert changes["exited"] == []

    def test_hysteresis_rank_within_buffer_not_exited(self, monkeypatch):
        """Rank 15 → rank 17 with buffer=5 → NOT in exited (17 <= 15+5=20)."""
        from iwda_fetcher import compute_changes

        top_n = 15
        buffer = 5
        # prior: T00..T14 at ranks 1-15
        prior = [f"T{i:02d}" for i in range(15)]

        # current: T14 has slipped to rank 17, a new ticker T15 takes rank 15
        # ranks: T00-T13 at 1-14, T15 at 15, T14 at 17, T16+ at 18+
        current_top_n = [f"T{i:02d}" for i in range(14)] + ["T15"]
        # We also need T14 to appear in the snapshot at rank 17 (beyond top_n=15 but within buffer)
        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(settings, "iwda_top_n", top_n)
        monkeypatch.setattr(settings, "iwda_exit_buffer", buffer)

        _seed_snapshot(prior, ts1)

        # Insert current snapshot manually to control T14's rank to 17
        with db_conn() as conn:
            for i, ticker in enumerate(current_top_n):
                conn.execute(
                    "INSERT OR IGNORE INTO iwda_holdings"
                    " (ticker, name, weight_pct, rank, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (ticker, f"{ticker} Inc", 5.0 - i * 0.1, i + 1, ts2.isoformat()),
                )
            # T14 at rank 17 (within buffer zone 16-20)
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("T14", "T14 Inc", 3.0, 17, ts2.isoformat()),
            )

        changes = compute_changes()
        exited_tickers = [item["ticker"] for item in changes["exited"]]
        new_tickers = [item["ticker"] for item in changes["new"]]
        assert "T14" not in exited_tickers
        assert "T15" in new_tickers

    def test_hysteresis_rank_beyond_buffer_is_exited(self, monkeypatch):
        """Rank 15 → rank 22 with buffer=5 → IS in exited (22 > 15+5=20)."""
        from iwda_fetcher import compute_changes

        top_n = 15
        buffer = 5
        prior = [f"T{i:02d}" for i in range(15)]

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(settings, "iwda_top_n", top_n)
        monkeypatch.setattr(settings, "iwda_exit_buffer", buffer)

        _seed_snapshot(prior, ts1)

        # current: T00-T13 unchanged, T15 fills rank 15, T14 is at rank 22
        with db_conn() as conn:
            for i in range(14):
                ticker = f"T{i:02d}"
                conn.execute(
                    "INSERT OR IGNORE INTO iwda_holdings"
                    " (ticker, name, weight_pct, rank, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (ticker, f"{ticker} Inc", 5.0 - i * 0.1, i + 1, ts2.isoformat()),
                )
            # T15 takes rank 15
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("T15", "T15 Inc", 2.0, 15, ts2.isoformat()),
            )
            # T14 is at rank 22 (beyond buffer)
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("T14", "T14 Inc", 1.5, 22, ts2.isoformat()),
            )

        changes = compute_changes()
        exited_tickers = [item["ticker"] for item in changes["exited"]]
        new_tickers = [item["ticker"] for item in changes["new"]]
        assert "T14" in exited_tickers
        assert "T15" in new_tickers

    def test_ticker_absent_from_current_snapshot_is_exited(self, monkeypatch):
        """Ticker in prior but completely absent from current snapshot → exited."""
        from iwda_fetcher import compute_changes

        top_n = 15
        prior = [f"T{i:02d}" for i in range(15)]
        current = [f"T{i:02d}" for i in range(14)] + ["T15"]

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(settings, "iwda_top_n", top_n)
        monkeypatch.setattr(settings, "iwda_exit_buffer", 5)

        _seed_snapshot(prior, ts1)
        _seed_snapshot(current, ts2)

        changes = compute_changes()
        exited_tickers = [item["ticker"] for item in changes["exited"]]
        assert "T14" in exited_tickers

    def test_rank_info_included_in_results(self, monkeypatch):
        """Each item in new/exited/kept includes ticker, current_rank, prior_rank."""
        from iwda_fetcher import compute_changes

        monkeypatch.setattr(settings, "iwda_top_n", 3)
        monkeypatch.setattr(settings, "iwda_exit_buffer", 5)

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        _seed_snapshot(["AAPL", "MSFT", "NVDA"], ts1)  # NVDA rank 3 in prior
        _seed_snapshot(["AAPL", "MSFT", "AMZN"], ts2)  # AMZN rank 3 in current; NVDA absent

        changes = compute_changes()

        # kept: AAPL and MSFT
        for item in changes["kept"]:
            assert "ticker" in item
            assert "current_rank" in item
            assert "prior_rank" in item
            assert item["prior_rank"] is not None
            assert item["current_rank"] is not None

        # new: AMZN (rank 3 current, prior_rank=None)
        new_items = {item["ticker"]: item for item in changes["new"]}
        assert "AMZN" in new_items
        assert new_items["AMZN"]["current_rank"] == 3
        assert new_items["AMZN"]["prior_rank"] is None

        # exited: NVDA (absent from current → current_rank=None)
        exited_items = {item["ticker"]: item for item in changes["exited"]}
        assert "NVDA" in exited_items
        assert exited_items["NVDA"]["current_rank"] is None
        assert exited_items["NVDA"]["prior_rank"] == 3

    def test_not_enough_snapshots_returns_empty(self, monkeypatch):
        """With only one snapshot, compute_changes returns empty dict."""
        from iwda_fetcher import compute_changes

        monkeypatch.setattr(settings, "iwda_top_n", 15)
        monkeypatch.setattr(settings, "iwda_exit_buffer", 5)
        ts = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        _seed_snapshot(["AAPL", "MSFT"], ts)

        changes = compute_changes()
        assert changes == {"new": [], "exited": [], "kept": []}

    def test_no_snapshots_returns_empty(self, monkeypatch):
        """With no snapshots at all, compute_changes returns empty dict."""
        from iwda_fetcher import compute_changes

        monkeypatch.setattr(settings, "iwda_top_n", 15)
        monkeypatch.setattr(settings, "iwda_exit_buffer", 5)

        changes = compute_changes()
        assert changes == {"new": [], "exited": [], "kept": []}

    def test_explicit_top_n_overrides_settings(self, monkeypatch):
        """Passing top_n explicitly overrides settings.iwda_top_n."""
        from iwda_fetcher import compute_changes

        monkeypatch.setattr(settings, "iwda_top_n", 15)
        monkeypatch.setattr(settings, "iwda_exit_buffer", 5)

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)

        # Only 3 tickers in each snapshot, use top_n=2
        _seed_snapshot(["AAPL", "MSFT", "NVDA"], ts1)
        _seed_snapshot(["AAPL", "MSFT", "AMZN"], ts2)

        changes = compute_changes(top_n=2)
        # Top 2 prior = AAPL, MSFT. Top 2 current = AAPL, MSFT. AMZN is rank 3.
        kept_tickers = [item["ticker"] for item in changes["kept"]]
        new_tickers = [item["ticker"] for item in changes["new"]]
        exited_tickers = [item["ticker"] for item in changes["exited"]]
        assert "AAPL" in kept_tickers
        assert "MSFT" in kept_tickers
        assert "AMZN" not in new_tickers  # rank 3 > top_n=2
        assert "NVDA" not in exited_tickers  # rank 3 in prior > top_n=2


# ---------------------------------------------------------------------------
# most_recent_fetched_at tests
# ---------------------------------------------------------------------------


class TestMostRecentFetchedAt:
    def test_returns_none_when_empty(self):
        from iwda_fetcher import most_recent_fetched_at

        assert most_recent_fetched_at() is None

    def test_returns_latest_timestamp(self):
        from iwda_fetcher import most_recent_fetched_at

        ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
        _seed_snapshot(["AAPL"], ts1)
        _seed_snapshot(["MSFT"], ts2)

        result = most_recent_fetched_at()
        assert result is not None
        # The returned datetime should match ts2 (most recent)
        assert result.replace(tzinfo=None) == ts2.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# fetch_and_save integration
# ---------------------------------------------------------------------------


class TestFetchAndSave:
    def test_fetch_and_save_persists(self, monkeypatch):
        import iwda_fetcher

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        sample = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts)
        ]
        monkeypatch.setattr(iwda_fetcher, "fetch_iwda_holdings", lambda: sample)
        monkeypatch.setattr(iwda_fetcher, "save_holdings", mock.Mock())

        result = iwda_fetcher.fetch_and_save()
        assert result == sample
        iwda_fetcher.save_holdings.assert_called_once_with(sample)


# ---------------------------------------------------------------------------
# CLI entrypoint smoke test
# ---------------------------------------------------------------------------


class TestCLI:
    def test_main_prints_summary(self, monkeypatch, capsys):
        import iwda_fetcher

        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        sample = [
            IwdaHolding(ticker="AAPL", name="Apple Inc", weight_pct=5.5, rank=1, fetched_at=ts),
            IwdaHolding(ticker="MSFT", name="Microsoft", weight_pct=4.9, rank=2, fetched_at=ts),
        ]
        monkeypatch.setattr(iwda_fetcher, "fetch_and_save", lambda: sample)

        iwda_fetcher.main()
        out = capsys.readouterr().out
        assert "IWDA" in out
        assert "2" in out  # count
        assert "AAPL" in out  # top ticker


# ---------------------------------------------------------------------------
# Integration test (skipped by default, requires network)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_integration_fetch_ishares_live():
    """Live test against the real iShares CSV endpoint.

    Skipped unless ``--with-integration`` is passed.
    Verifies the iShares URL and CSV format are still parseable.
    """
    from iwda_fetcher import _fetch_ishares

    holdings = _fetch_ishares()
    assert len(holdings) >= 10
    tickers = [h.ticker for h in holdings]
    assert any(t in tickers for t in ("AAPL", "MSFT", "NVDA"))
