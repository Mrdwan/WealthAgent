"""Shared test configuration and fixtures."""

import os
import tempfile

# ---------------------------------------------------------------------------
# Bootstrap — point DB at a temp file before any project imports.
# No API key stubs needed — settings doesn't validate on import.
# ---------------------------------------------------------------------------
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp_db.name

import pytest  # noqa: E402

from db import get_conn, init_db  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add opt-in flags for tests that require live external services."""
    parser.addoption(
        "--with-ollama",
        action="store_true",
        default=False,
        help="Run tests that require a live Ollama instance (skipped by default).",
    )
    parser.addoption(
        "--with-integration",
        action="store_true",
        default=False,
        help="Run tests that require live network access (skipped by default).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live-service tests unless the corresponding flag is passed."""
    if not config.getoption("--with-ollama"):
        skip_ollama = pytest.mark.skip(reason="requires --with-ollama to run")
        for item in items:
            if item.get_closest_marker("ollama"):
                item.add_marker(skip_ollama)
    if not config.getoption("--with-integration"):
        skip_integration = pytest.mark.skip(reason="requires --with-integration to run")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip_integration)


# Create tables once at import time so the autouse fixture can DELETE safely.
init_db()


@pytest.fixture(autouse=True)
def _fresh_db():
    """Ensure clean DB state for each test."""
    conn = get_conn()
    try:
        for table in (
            "reports",
            "alerts_log",
            "alert_config",
            "news_signals",
            "news_articles",
            "price_history",
            "fx_rates",
            "trades",
            "holdings",
            "tax_year",
            "iwda_holdings",
        ):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        conn.commit()
    finally:
        conn.close()
    yield
