"""Shared test configuration and fixtures."""

import os
import tempfile

# ---------------------------------------------------------------------------
# Bootstrap — must run before any project imports
# ---------------------------------------------------------------------------
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp_db.name

for _key, _val in {
    "TIINGO_API_KEY": "test-key",
    "ANTHROPIC_API_KEY": "test-key",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHAT_ID": "0",
}.items():
    os.environ.setdefault(_key, _val)

import pytest  # noqa: E402

from db import get_conn, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Ensure clean DB state for each test."""
    conn = get_conn()
    try:
        for table in (
            "alerts_log",
            "news_signals",
            "news_articles",
            "fundamentals",
            "price_history",
            "fx_rates",
            "screener_candidates",
            "trades",
            "holdings",
            "tax_year",
        ):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        conn.commit()
    finally:
        conn.close()
    init_db()
    yield
