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
    """Add --with-ollama flag to enable live Ollama integration tests."""
    parser.addoption(
        "--with-ollama",
        action="store_true",
        default=False,
        help="Run tests that require a live Ollama instance (skipped by default).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked @pytest.mark.ollama unless --with-ollama is passed."""
    if not config.getoption("--with-ollama"):
        skip = pytest.mark.skip(reason="requires --with-ollama to run")
        for item in items:
            if item.get_closest_marker("ollama"):
                item.add_marker(skip)


# Create tables once at import time so the autouse fixture can DELETE safely.
init_db()


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
    yield
