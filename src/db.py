"""Database initialization and connection management for WealthAgent."""

import contextlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime

from pydantic import BaseModel, Field

from config.settings import settings

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    """A single position in the portfolio."""

    id: int | None = None
    ticker: str
    shares: float
    entry_price_usd: float | None = None
    entry_price_eur: float
    entry_fx_rate: float
    purchase_date: date
    broker: str = "Revolut"
    pool: str = Field(..., pattern="^(long_term|short_term|bond)$")
    notes: str | None = None


class PricePoint(BaseModel):
    """A daily closing price for a ticker."""

    id: int | None = None
    ticker: str
    date: date
    close_usd: float | None = None
    close_eur: float | None = None
    source: str = "tiingo"


class FxRate(BaseModel):
    """A daily FX rate for a currency pair (e.g. EURUSD)."""

    id: int | None = None
    date: date
    pair: str
    rate: float


class Fundamentals(BaseModel):
    """Fundamental data snapshot for a ticker."""

    id: int | None = None
    ticker: str
    fetched_at: datetime
    pe_ratio: float | None = None
    ps_ratio: float | None = None
    revenue_growth: float | None = None
    profit_margin: float | None = None
    free_cash_flow: float | None = None
    debt_to_equity: float | None = None
    dividend_yield: float | None = None
    market_cap: float | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    next_earnings: date | None = None
    raw_json: str | None = None  # full API response


class NewsArticle(BaseModel):
    """A news article fetched from an RSS feed or news API."""

    id: int | None = None
    url: str
    title: str | None = None
    source: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    content_snippet: str | None = None
    processed: int = 0  # 0 = pending, 1 = processed by LLM


class NewsSignal(BaseModel):
    """An LLM-extracted investment signal from a news article.

    ``tickers`` is stored in the DB as a JSON array string; the model
    exposes it as a Python list for convenience.
    """

    id: int | None = None
    article_id: int
    tickers: list[str] = Field(default_factory=list)
    sentiment: str | None = None  # bullish | bearish | neutral
    catalyst: str | None = None
    timeframe: str | None = None
    summary: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    extracted_at: datetime | None = None

    def tickers_json(self) -> str:
        """Serialise tickers list to JSON string for DB storage."""
        return json.dumps(self.tickers)


class Trade(BaseModel):
    """A buy or sell transaction."""

    id: int | None = None
    date: date
    action: str = Field(..., pattern="^(buy|sell)$")
    ticker: str
    amount_eur: float | None = None
    amount_usd: float | None = None
    price_usd: float | None = None
    price_eur: float | None = None
    fx_rate: float | None = None
    shares: float | None = None
    realized_gain_eur: float | None = None
    cgt_paid: float | None = None
    pool: str | None = None
    notes: str | None = None


class TaxYear(BaseModel):
    """Aggregated CGT tracking for a single tax year."""

    id: int | None = None
    year: int
    realized_gains_eur: float = 0.0
    exemption_used: float = 0.0


class ScreenerCandidate(BaseModel):
    """A stock surfaced by the screener and awaiting review."""

    id: int | None = None
    ticker: str
    screened_at: datetime | None = None
    market_cap: float | None = None
    revenue_growth: float | None = None
    pe_ratio: float | None = None
    sector: str | None = None
    country: str | None = None
    llm_score: float | None = Field(None, ge=0.0, le=10.0)
    llm_thesis: str | None = None
    llm_risk: str | None = None
    dividend_yield: float | None = None
    debt_to_equity: float | None = None
    status: str = "pending"  # pending | reviewed | added | rejected


class AlertLog(BaseModel):
    """Record of a triggered alert."""

    id: int | None = None
    triggered_at: datetime | None = None
    ticker: str | None = None
    alert_type: str | None = None  # price_drop | news_signal | opportunity
    details: str | None = None
    action_taken: str | None = None


class AlertConfig(BaseModel):
    """A runtime-configurable alert threshold stored in the database."""

    key: str
    value: str


class Report(BaseModel):
    """A saved LLM advisor report."""

    id: int | None = None
    created_at: datetime | None = None
    report_type: str = Field(..., pattern="^(rebalance|analyze)$")
    ticker: str | None = None
    summary: str
    full_content: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    """Open and return a WAL-mode SQLite connection with foreign keys enabled."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a connection, commits on success, rolls back on error."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    id               INTEGER PRIMARY KEY,
    ticker           TEXT    NOT NULL,
    shares           REAL    NOT NULL,
    entry_price_usd  REAL,
    entry_price_eur  REAL    NOT NULL,
    entry_fx_rate    REAL    NOT NULL,
    purchase_date    TEXT    NOT NULL,
    broker           TEXT    DEFAULT 'Revolut',
    pool             TEXT    NOT NULL CHECK(pool IN ('long_term','short_term','bond')),
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id        INTEGER PRIMARY KEY,
    ticker    TEXT NOT NULL,
    date      TEXT NOT NULL,
    close_usd REAL,
    close_eur REAL,
    source    TEXT NOT NULL DEFAULT 'tiingo',
    UNIQUE(ticker, date, source)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    id   INTEGER PRIMARY KEY,
    date TEXT    NOT NULL,
    pair TEXT    NOT NULL,
    rate REAL    NOT NULL,
    UNIQUE(date, pair)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    pe_ratio        REAL,
    ps_ratio        REAL,
    revenue_growth  REAL,
    profit_margin   REAL,
    free_cash_flow  REAL,
    debt_to_equity  REAL,
    dividend_yield  REAL,
    market_cap      REAL,
    sector          TEXT,
    industry        TEXT,
    country         TEXT,
    next_earnings   TEXT,
    raw_json        TEXT
);

CREATE TABLE IF NOT EXISTS news_articles (
    id               INTEGER PRIMARY KEY,
    url              TEXT UNIQUE NOT NULL,
    title            TEXT,
    source           TEXT,
    published_at     TEXT,
    fetched_at       TEXT DEFAULT (datetime('now')),
    content_snippet  TEXT,
    processed        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS news_signals (
    id           INTEGER PRIMARY KEY,
    article_id   INTEGER NOT NULL REFERENCES news_articles(id),
    tickers      TEXT,
    sentiment    TEXT,
    catalyst     TEXT,
    timeframe    TEXT,
    summary      TEXT,
    confidence   REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    extracted_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY,
    date              TEXT NOT NULL,
    action            TEXT NOT NULL CHECK(action IN ('buy','sell')),
    ticker            TEXT NOT NULL,
    amount_eur        REAL,
    amount_usd        REAL,
    price_usd         REAL,
    price_eur         REAL,
    fx_rate           REAL,
    shares            REAL,
    realized_gain_eur REAL,
    cgt_paid          REAL,
    pool              TEXT,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS tax_year (
    id                  INTEGER PRIMARY KEY,
    year                INTEGER NOT NULL,
    realized_gains_eur  REAL DEFAULT 0,
    exemption_used      REAL DEFAULT 0,
    UNIQUE(year)
);

CREATE TABLE IF NOT EXISTS screener_candidates (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL,
    screened_at     TEXT DEFAULT (datetime('now')),
    market_cap      REAL,
    revenue_growth  REAL,
    pe_ratio        REAL,
    sector          TEXT,
    country         TEXT,
    llm_score       REAL CHECK(llm_score IS NULL OR (llm_score >= 0 AND llm_score <= 10)),
    llm_thesis      TEXT,
    status          TEXT DEFAULT 'pending'
                    CHECK(status IN ('pending','reviewed','added','rejected'))
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id           INTEGER PRIMARY KEY,
    triggered_at TEXT DEFAULT (datetime('now')),
    ticker       TEXT,
    alert_type   TEXT,
    details      TEXT,
    action_taken TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    report_type  TEXT NOT NULL CHECK(report_type IN ('rebalance', 'analyze')),
    ticker       TEXT,
    summary      TEXT NOT NULL,
    full_content TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_expires_at ON reports(expires_at);

CREATE TABLE IF NOT EXISTS alert_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create all tables (idempotent) and seed the current tax year row."""
    conn = get_conn()
    try:
        # executescript issues an implicit COMMIT before running, so DDL is
        # safe even inside an open transaction.
        conn.executescript(_SCHEMA)

        # Migrate: add optional columns to screener_candidates
        for col, col_type in [
            ("llm_risk", "TEXT"),
            ("dividend_yield", "REAL"),
            ("debt_to_equity", "REAL"),
        ]:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(f"ALTER TABLE screener_candidates ADD COLUMN {col} {col_type}")

        conn.execute(
            "INSERT OR IGNORE INTO tax_year (year, realized_gains_eur, exemption_used)"
            " VALUES (?, 0, 0)",
            (datetime.now().year,),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {settings.db_path}")
