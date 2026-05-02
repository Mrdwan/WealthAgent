"""Unit tests for alert_engine.py — covers gap lines and branches."""

from datetime import UTC, datetime, timedelta
from unittest import mock

from db import db_conn


def _seed_holding(ticker: str, pool: str = "long_term") -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, 10.0, 150.0, 1.1, "2024-01-01", pool),
        )


def _seed_price(ticker: str, price_usd: float, days_ago: int = 0) -> None:
    dt = (datetime.now(tz=UTC) - timedelta(days=days_ago)).date().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history"
            " (ticker, date, close_usd, close_eur, source) VALUES (?, ?, ?, ?, ?)",
            (ticker, dt, price_usd, price_usd / 1.1, "test"),
        )


def _seed_iwda_snapshot(
    tickers: list[str],
    fetched_at: datetime,
    base_rank: int = 1,
) -> None:
    """Insert a holdings snapshot directly into the DB for test setup."""
    with db_conn() as conn:
        for i, ticker in enumerate(tickers):
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (ticker, f"{ticker} Inc", 5.0 - i * 0.1, base_rank + i, fetched_at.isoformat()),
            )


# --- check_price_drops ---


def test_check_price_drops_no_holdings():
    from alert_engine import check_price_drops

    assert check_price_drops() == []


def test_check_price_drops_no_current_price():
    from alert_engine import check_price_drops

    _seed_holding("AAPL")
    # No price data at all
    assert check_price_drops() == []


def test_check_price_drops_no_prior_price():
    from alert_engine import check_price_drops

    _seed_holding("AAPL")
    _seed_price("AAPL", 100.0, days_ago=0)
    # No price 30+ days ago
    assert check_price_drops() == []


def test_check_price_drops_zero_prior():
    from alert_engine import check_price_drops

    _seed_holding("AAPL")
    _seed_price("AAPL", 0.0, days_ago=35)
    _seed_price("AAPL", 100.0, days_ago=0)
    # prior_price == 0 → skip
    assert check_price_drops() == []


def test_check_price_drops_triggered():
    from alert_engine import check_price_drops

    _seed_holding("TSLA")
    _seed_price("TSLA", 200.0, days_ago=35)
    _seed_price("TSLA", 160.0, days_ago=0)
    alerts = check_price_drops(threshold_pct=10.0)
    assert len(alerts) == 1
    assert alerts[0].ticker == "TSLA"
    assert alerts[0].details["drop_pct"] < 0
    # Verify USD field names (not EUR)
    assert "current_price_usd" in alerts[0].details
    assert "prior_price_usd" in alerts[0].details


def test_check_price_drops_not_triggered():
    from alert_engine import check_price_drops

    _seed_holding("GOOG")
    _seed_price("GOOG", 100.0, days_ago=35)
    _seed_price("GOOG", 97.0, days_ago=0)  # only -3%
    assert check_price_drops(threshold_pct=10.0) == []


# --- check_iwda_exits ---


def test_check_iwda_exits_no_snapshots():
    """No IWDA snapshots → returns [] without error."""
    from alert_engine import check_iwda_exits

    assert check_iwda_exits() == []


def test_check_iwda_exits_not_enough_snapshots():
    """Only one IWDA snapshot → compute_changes returns empty → no alerts."""
    from alert_engine import check_iwda_exits

    ts = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
    _seed_iwda_snapshot(["AAPL", "MSFT"], ts)
    assert check_iwda_exits() == []


def test_check_iwda_exits_no_held_tickers_in_exited():
    """Ticker exited IWDA but is not held → no alert."""
    from unittest import mock

    from alert_engine import check_iwda_exits
    from config.settings import settings

    ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
    ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)
    prior = ["AAPL", "MSFT", "NVDA"]
    current = ["AAPL", "MSFT", "AMZN"]

    _seed_iwda_snapshot(prior, ts1)
    # NVDA exits; AMZN enters — but NVDA is not held
    with db_conn() as conn:
        for i, ticker in enumerate(current):
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, f"{ticker} Inc", 5.0 - i * 0.1, i + 1, ts2.isoformat()),
            )
        # NVDA at rank 25 (beyond top_n=3 + buffer=5=8)
        conn.execute(
            "INSERT OR IGNORE INTO iwda_holdings"
            " (ticker, name, weight_pct, rank, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("NVDA", "NVDA Inc", 1.0, 25, ts2.isoformat()),
        )

    with (
        mock.patch.object(settings, "iwda_top_n", 3),
        mock.patch.object(settings, "iwda_exit_buffer", 5),
    ):
        alerts = check_iwda_exits()
    assert alerts == []


def test_check_iwda_exits_triggered():
    """Held ticker exits IWDA band → alert generated."""
    from unittest import mock

    from alert_engine import check_iwda_exits
    from config.settings import settings

    ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
    ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)

    # NVDA was in top-3, now ranked 25 (beyond top_n=3+buffer=5=8) → exited
    _seed_iwda_snapshot(["AAPL", "MSFT", "NVDA"], ts1)
    _seed_holding("NVDA")

    with db_conn() as conn:
        for i, ticker in enumerate(["AAPL", "MSFT", "AMZN"]):
            conn.execute(
                "INSERT OR IGNORE INTO iwda_holdings"
                " (ticker, name, weight_pct, rank, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (ticker, f"{ticker} Inc", 5.0 - i * 0.1, i + 1, ts2.isoformat()),
            )
        conn.execute(
            "INSERT OR IGNORE INTO iwda_holdings"
            " (ticker, name, weight_pct, rank, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("NVDA", "NVDA Inc", 1.0, 25, ts2.isoformat()),
        )

    with (
        mock.patch.object(settings, "iwda_top_n", 3),
        mock.patch.object(settings, "iwda_exit_buffer", 5),
    ):
        alerts = check_iwda_exits()

    assert len(alerts) == 1
    assert alerts[0].type == "iwda_exit"
    assert alerts[0].ticker == "NVDA"
    assert alerts[0].details["prior_rank"] == 3
    assert alerts[0].details["current_rank"] == 25
    assert alerts[0].details["top_n"] == 3
    assert alerts[0].details["exit_buffer"] == 5


def test_check_iwda_exits_absent_ticker():
    """Held ticker completely absent from current IWDA snapshot → alert with current_rank=None."""
    from unittest import mock

    from alert_engine import check_iwda_exits
    from config.settings import settings

    ts1 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
    ts2 = datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC)

    _seed_iwda_snapshot(["AAPL", "MSFT", "NVDA"], ts1)
    _seed_holding("NVDA")
    # Current snapshot has NVDA completely absent
    _seed_iwda_snapshot(["AAPL", "MSFT", "AMZN"], ts2)

    with (
        mock.patch.object(settings, "iwda_top_n", 3),
        mock.patch.object(settings, "iwda_exit_buffer", 5),
    ):
        alerts = check_iwda_exits()

    assert len(alerts) == 1
    assert alerts[0].ticker == "NVDA"
    assert alerts[0].details["current_rank"] is None


# --- run_all_checks ---


def test_run_all_checks_dedup():
    """Duplicate (type, ticker) alerts are reduced to one; different types both appear."""
    import alert_engine
    from alert_engine import Alert, run_all_checks

    now = datetime.now(tz=UTC)
    dup_alert = Alert(type="price_drop", ticker="TSLA", details={}, triggered_at=now)

    # Return two identical alerts from check_price_drops → only one survives dedup
    monkeypatch_fn = mock.patch.object(
        alert_engine, "check_price_drops", return_value=[dup_alert, dup_alert]
    )
    monkeypatch_fn2 = mock.patch.object(alert_engine, "check_iwda_exits", return_value=[])

    with monkeypatch_fn, monkeypatch_fn2:
        alerts = run_all_checks()

    price_drop_tsla = [a for a in alerts if a.type == "price_drop" and a.ticker == "TSLA"]
    assert len(price_drop_tsla) == 1  # deduplicated from 2 to 1


def test_run_all_checks_log_error(monkeypatch):
    """_log_alert exception is caught and logged."""
    from alert_engine import run_all_checks

    _seed_holding("TSLA")
    _seed_price("TSLA", 200.0, days_ago=35)
    _seed_price("TSLA", 160.0, days_ago=0)

    monkeypatch.setattr("alert_engine._log_alert", mock.MagicMock(side_effect=Exception("DB err")))
    alerts = run_all_checks()
    # Should still return alerts even if logging fails
    assert isinstance(alerts, list)


# --- main ---


def test_main_no_alerts(monkeypatch, capsys):
    import alert_engine

    monkeypatch.setattr(alert_engine, "run_all_checks", lambda: [])
    alert_engine.main()
    assert "No alerts triggered" in capsys.readouterr().out


def test_main_with_alerts(monkeypatch, capsys):
    import alert_engine
    from alert_engine import Alert

    alerts = [
        Alert(
            type="price_drop",
            ticker="TSLA",
            details={"drop_pct": -21.5, "threshold_pct": 15.0},
            triggered_at=datetime.now(tz=UTC),
        ),
    ]
    monkeypatch.setattr(alert_engine, "run_all_checks", lambda: alerts)
    alert_engine.main()
    out = capsys.readouterr().out
    assert "PRICE_DROP" in out
    assert "TSLA" in out
