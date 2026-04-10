"""Unit tests for db.py — covers tickers_json and db_conn rollback."""

import json

import pytest

from db import NewsSignal, db_conn, get_conn, init_db


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
