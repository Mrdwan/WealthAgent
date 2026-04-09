"""Unit tests for alert_engine.py — covers gap lines and branches."""

import json
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


def _seed_price(ticker: str, price_eur: float, days_ago: int = 0) -> None:
    dt = (datetime.now(tz=UTC) - timedelta(days=days_ago)).date().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history"
            " (ticker, date, close_usd, close_eur, source) VALUES (?, ?, ?, ?, ?)",
            (ticker, dt, price_eur * 1.1, price_eur, "test"),
        )


def _seed_signal(
    tickers: list[str],
    sentiment: str = "negative",
    confidence: float = 0.8,
    hours_ago: int = 1,
    tickers_raw: str | None = None,
) -> int:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, title, processed) VALUES (?, ?, ?)",
            (f"https://example.com/{id(tickers)}", "t", 1),
        )
    from db import get_conn

    conn2 = get_conn()
    try:
        article_id = conn2.execute(
            "SELECT id FROM news_articles ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    finally:
        conn2.close()

    dt = (datetime.now(tz=UTC) - timedelta(hours=hours_ago)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_signals"
            " (article_id, tickers, sentiment, catalyst, timeframe, summary,"
            "  confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                article_id,
                tickers_raw if tickers_raw is not None else json.dumps(tickers),
                sentiment,
                "earnings",
                "weeks",
                "Test summary",
                confidence,
                dt,
            ),
        )
    return article_id


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


def test_check_price_drops_not_triggered():
    from alert_engine import check_price_drops

    _seed_holding("GOOG")
    _seed_price("GOOG", 100.0, days_ago=35)
    _seed_price("GOOG", 97.0, days_ago=0)  # only -3%
    assert check_price_drops(threshold_pct=10.0) == []


# --- check_news_signals ---


def test_check_news_signals_no_holdings():
    from alert_engine import check_news_signals

    assert check_news_signals() == []


def test_check_news_signals_triggered():
    from alert_engine import check_news_signals

    _seed_holding("NVDA")
    _seed_signal(["NVDA"], sentiment="negative", confidence=0.8)
    alerts = check_news_signals(hours=24)
    assert len(alerts) == 1
    assert alerts[0].type == "news_signal"


def test_check_news_signals_json_error():
    from alert_engine import check_news_signals

    _seed_holding("NVDA")
    _seed_signal(["NVDA"], sentiment="negative", confidence=0.8, tickers_raw="bad-json")
    alerts = check_news_signals(hours=24)
    # Malformed tickers → empty list → no overlap → skipped
    assert len(alerts) == 0


def test_check_news_signals_no_overlap():
    from alert_engine import check_news_signals

    _seed_holding("AAPL")
    _seed_signal(["GOOG"], sentiment="negative", confidence=0.8)
    # Signal tickers don't overlap with held tickers
    assert check_news_signals(hours=24) == []


# --- check_opportunities ---


def test_check_opportunities_triggered():
    from alert_engine import check_opportunities

    _seed_signal(["AMZN"], sentiment="positive", confidence=0.85)
    alerts = check_opportunities(hours=24)
    assert len(alerts) == 1
    assert alerts[0].type == "opportunity"


def test_check_opportunities_held_excluded():
    from alert_engine import check_opportunities

    _seed_holding("AMZN")
    _seed_signal(["AMZN"], sentiment="positive", confidence=0.85)
    assert check_opportunities(hours=24) == []


def test_check_opportunities_json_error():
    from alert_engine import check_opportunities

    _seed_signal(["X"], sentiment="positive", confidence=0.85, tickers_raw="bad")
    assert check_opportunities(hours=24) == []


# --- run_all_checks ---


def test_run_all_checks_dedup():
    from alert_engine import run_all_checks

    _seed_holding("TSLA")
    _seed_price("TSLA", 200.0, days_ago=35)
    _seed_price("TSLA", 160.0, days_ago=0)
    # Two negative signals for TSLA → two news_signal alerts → deduplicated to one
    _seed_signal(["TSLA"], sentiment="negative", confidence=0.8, hours_ago=1)
    _seed_signal(["TSLA"], sentiment="negative", confidence=0.9, hours_ago=2)

    alerts = run_all_checks()
    news_alerts = [a for a in alerts if a.type == "news_signal" and a.ticker == "TSLA"]
    assert len(news_alerts) == 1  # deduplicated


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
            details={"drop_pct": -21.5, "threshold_pct": 10.0},
            triggered_at=datetime.now(tz=UTC),
        ),
    ]
    monkeypatch.setattr(alert_engine, "run_all_checks", lambda: alerts)
    alert_engine.main()
    out = capsys.readouterr().out
    assert "PRICE_DROP" in out
    assert "TSLA" in out
