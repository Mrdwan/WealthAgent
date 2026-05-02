"""Unit tests for db.py — covers tickers_json and db_conn rollback."""

import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from db import AlertConfig, IwdaHolding, NewsSignal, Report, db_conn, get_conn, init_db


def test_tickers_json():
    signal = NewsSignal(article_id=1, tickers=["AAPL", "MSFT"])
    result = signal.tickers_json()
    assert json.loads(result) == ["AAPL", "MSFT"]


def test_tickers_json_empty():
    signal = NewsSignal(article_id=1, tickers=[])
    assert signal.tickers_json() == "[]"


def test_db_conn_rollback():
    """db_conn rolls back on exception — the insert should not persist."""
    with pytest.raises(RuntimeError), db_conn() as conn:
        conn.execute(
            "INSERT INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("FAIL", 1.0, 100.0, 1.1, "2024-01-01", "long_term"),
        )
        raise RuntimeError("Simulated error")

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM holdings WHERE ticker = 'FAIL'").fetchone()
    finally:
        conn.close()
    assert row is None


def test_init_db_idempotent():
    init_db()
    init_db()  # should not raise


def test_init_db_creates_reports_table():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reports'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_report_model_valid():
    expires = datetime.now() + timedelta(days=90)
    r = Report(
        report_type="rebalance",
        summary="Hold cash.",
        full_content="Detailed analysis here.",
        expires_at=expires,
    )
    assert r.report_type == "rebalance"
    assert r.id is None
    assert r.ticker is None


def test_report_model_analyze_type():
    expires = datetime.now() + timedelta(days=90)
    r = Report(
        report_type="analyze",
        ticker="AAPL",
        summary="Buy AAPL.",
        full_content="Full analysis.",
        expires_at=expires,
    )
    assert r.report_type == "analyze"
    assert r.ticker == "AAPL"


def test_report_model_invalid_type():
    expires = datetime.now() + timedelta(days=90)
    with pytest.raises(ValidationError):
        Report(
            report_type="invalid",
            summary="test",
            full_content="test",
            expires_at=expires,
        )


def test_init_db_creates_alert_config_table():
    """alert_config table exists after init_db()."""
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_config'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_alert_config_model():
    """AlertConfig model validates key and value fields."""
    cfg = AlertConfig(key="alert_drop_pct", value="5.0")
    assert cfg.key == "alert_drop_pct"
    assert cfg.value == "5.0"


def test_iwda_holding_model():
    """IwdaHolding model accepts valid fields and defaults id to None."""
    now = datetime(2025, 5, 1, 12, 0, 0)
    holding = IwdaHolding(
        ticker="AAPL",
        name="Apple Inc.",
        weight_pct=5.23,
        rank=1,
        fetched_at=now,
    )
    assert holding.id is None
    assert holding.ticker == "AAPL"
    assert holding.name == "Apple Inc."
    assert holding.weight_pct == 5.23
    assert holding.rank == 1
    assert holding.fetched_at == now


def test_iwda_holding_model_with_id():
    """IwdaHolding model accepts an explicit id."""
    now = datetime(2025, 5, 1, 12, 0, 0)
    holding = IwdaHolding(
        id=42,
        ticker="MSFT",
        name="Microsoft Corp.",
        weight_pct=4.10,
        rank=2,
        fetched_at=now,
    )
    assert holding.id == 42


def test_init_db_creates_iwda_holdings_table():
    """iwda_holdings table exists after init_db()."""
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iwda_holdings'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_init_db_creates_iwda_holdings_index():
    """idx_iwda_holdings_fetched_at index exists after init_db()."""
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='index' AND name='idx_iwda_holdings_fetched_at'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_iwda_holdings_unique_constraint():
    """Inserting duplicate (ticker, fetched_at) raises an IntegrityError."""
    import sqlite3

    fetched_at = "2025-05-01T12:00:00"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("AAPL", "Apple Inc.", 5.23, 1, fetched_at),
        )

    with pytest.raises(sqlite3.IntegrityError), db_conn() as conn:
        conn.execute(
            "INSERT INTO iwda_holdings (ticker, name, weight_pct, rank, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("AAPL", "Apple Inc.", 5.23, 1, fetched_at),
        )
