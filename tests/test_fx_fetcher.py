"""Unit tests for fx_fetcher.py."""

from datetime import date
from unittest import mock

import pytest

from db import FxRate, db_conn

# Valid ECB XML for mocking
_VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2024-04-08">
      <Cube currency="USD" rate="1.0876"/>
      <Cube currency="GBP" rate="0.8553"/>
      <Cube currency="JPY" rate="164.50"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""

_NO_RATES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2024-04-08"></Cube></Cube>
</gesmes:Envelope>"""

_NO_CUBE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube></Cube>
</gesmes:Envelope>"""


def _mock_response(text):
    resp = mock.MagicMock()
    resp.text = text
    resp.raise_for_status = mock.MagicMock()
    return resp


# --- fetch_ecb_rates ---


def test_fetch_ecb_rates_valid():
    import fx_fetcher

    with mock.patch.object(fx_fetcher.requests, "get", return_value=_mock_response(_VALID_XML)):
        rates = fx_fetcher.fetch_ecb_rates()
    assert len(rates) == 3
    assert any(r.pair == "EURUSD" for r in rates)


def test_fetch_ecb_rates_no_cube():
    """ValueError when no dated Cube element."""
    import fx_fetcher

    with (
        mock.patch.object(fx_fetcher.requests, "get", return_value=_mock_response(_NO_CUBE_XML)),
        pytest.raises(ValueError, match="Could not find dated Cube"),
    ):
        fx_fetcher.fetch_ecb_rates()


def test_fetch_ecb_rates_no_currency_entries():
    """Empty list when Cube has no currency entries."""
    import fx_fetcher

    with mock.patch.object(fx_fetcher.requests, "get", return_value=_mock_response(_NO_RATES_XML)):
        rates = fx_fetcher.fetch_ecb_rates()
    assert rates == []


# --- get_latest_rate ---


def test_get_latest_rate_not_found():
    from fx_fetcher import get_latest_rate

    with pytest.raises(ValueError, match="No FX rate found for pair"):
        get_latest_rate("EURXYZ")


# --- get_rate_for_date ---


def test_get_rate_for_date_not_found():
    from fx_fetcher import get_rate_for_date

    with pytest.raises(ValueError, match="No FX rate found"):
        get_rate_for_date("EURUSD", "2020-01-01")


def test_get_rate_for_date_string_input():
    from fx_fetcher import get_rate_for_date

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)",
            ("2024-04-08", "EURUSD", 1.0876),
        )
    assert get_rate_for_date("EURUSD", "2024-04-10") == pytest.approx(1.0876)


# --- usd_to_eur ---


def _seed_fx(pair: str, rate: float, dt: str = "2024-04-08") -> None:
    with db_conn() as conn:
        conn.execute("INSERT INTO fx_rates (date, pair, rate) VALUES (?, ?, ?)", (dt, pair, rate))


def test_usd_to_eur_no_date():
    from fx_fetcher import usd_to_eur

    _seed_fx("EURUSD", 1.1)
    assert usd_to_eur(110.0) == pytest.approx(100.0)


def test_usd_to_eur_with_date():
    from fx_fetcher import usd_to_eur

    _seed_fx("EURUSD", 1.1)
    assert usd_to_eur(110.0, on_date="2024-04-08") == pytest.approx(100.0)


# --- gbp_to_eur ---


def test_gbp_to_eur_no_date():
    from fx_fetcher import gbp_to_eur

    _seed_fx("EURGBP", 0.85)
    assert gbp_to_eur(85.0) == pytest.approx(100.0)


def test_gbp_to_eur_with_date():
    from fx_fetcher import gbp_to_eur

    _seed_fx("EURGBP", 0.85)
    assert gbp_to_eur(85.0, on_date=date(2024, 4, 8)) == pytest.approx(100.0)


# --- main ---


def test_main_no_rates(monkeypatch, capsys):
    import fx_fetcher

    monkeypatch.setattr(fx_fetcher, "fetch_ecb_rates", lambda: [])
    fx_fetcher.main()
    assert "No rates fetched" in capsys.readouterr().out


def test_main_with_rates(monkeypatch, capsys):
    import fx_fetcher

    mock_rates = [
        FxRate(date=date(2024, 4, 8), pair="EURUSD", rate=1.0876),
        FxRate(date=date(2024, 4, 8), pair="EURGBP", rate=0.8553),
        FxRate(date=date(2024, 4, 8), pair="EURJPY", rate=164.5),
    ]
    monkeypatch.setattr(fx_fetcher, "fetch_ecb_rates", lambda: mock_rates)
    # Seed DB so get_latest_rate works
    _seed_fx("EURUSD", 1.0876)
    _seed_fx("EURGBP", 0.8553)

    fx_fetcher.main()
    out = capsys.readouterr().out
    assert "EURUSD" in out
    assert "EURGBP" in out
    assert "*" in out  # highlighted pairs


def test_main_get_latest_rate_raises(monkeypatch):
    """except ValueError branches in main() when no rates in DB."""
    import fx_fetcher

    mock_rates = [FxRate(date=date(2024, 4, 8), pair="EURJPY", rate=164.5)]
    monkeypatch.setattr(fx_fetcher, "fetch_ecb_rates", lambda: mock_rates)
    # No EURUSD/EURGBP in DB → except ValueError catches
    fx_fetcher.main()
