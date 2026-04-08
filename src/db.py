import sqlite3
from datetime import datetime
from config.settings import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            entry_price_usd REAL,
            entry_price_eur REAL NOT NULL,
            entry_fx_rate REAL NOT NULL,
            purchase_date TEXT NOT NULL,
            broker TEXT DEFAULT 'Revolut',
            pool TEXT CHECK(pool IN ('long_term','short_term','bond')) NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close_usd REAL,
            close_eur REAL,
            source TEXT DEFAULT 'tiingo',
            UNIQUE(ticker, date, source)
        );

        CREATE TABLE IF NOT EXISTS fx_rates (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            pair TEXT NOT NULL,
            rate REAL NOT NULL,
            UNIQUE(date, pair)
        );

        CREATE TABLE IF NOT EXISTS fundamentals (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            pe_ratio REAL,
            ps_ratio REAL,
            revenue_growth REAL,
            profit_margin REAL,
            free_cash_flow REAL,
            debt_to_equity REAL,
            dividend_yield REAL,
            market_cap REAL,
            sector TEXT,
            industry TEXT,
            country TEXT,
            next_earnings TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            title TEXT,
            source TEXT,
            published_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            content_snippet TEXT,
            processed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS news_signals (
            id INTEGER PRIMARY KEY,
            article_id INTEGER REFERENCES news_articles(id),
            tickers TEXT,           -- JSON array
            sentiment TEXT,
            catalyst TEXT,
            timeframe TEXT,
            summary TEXT,
            confidence REAL,        -- 0-1 from multi-run scoring
            extracted_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            action TEXT CHECK(action IN ('buy','sell')) NOT NULL,
            ticker TEXT NOT NULL,
            amount_eur REAL,
            amount_usd REAL,
            price_usd REAL,
            price_eur REAL,
            fx_rate REAL,
            shares REAL,
            realized_gain_eur REAL,
            cgt_paid REAL,
            pool TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS tax_year (
            id INTEGER PRIMARY KEY,
            year INTEGER NOT NULL,
            realized_gains_eur REAL DEFAULT 0,
            exemption_used REAL DEFAULT 0,
            UNIQUE(year)
        );

        CREATE TABLE IF NOT EXISTS screener_candidates (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            screened_at TEXT DEFAULT (datetime('now')),
            market_cap REAL,
            revenue_growth REAL,
            pe_ratio REAL,
            sector TEXT,
            country TEXT,
            llm_score REAL,         -- 0-10 from Gemma evaluation
            llm_thesis TEXT,
            status TEXT DEFAULT 'pending'  -- pending, reviewed, added, rejected
        );

        CREATE TABLE IF NOT EXISTS alerts_log (
            id INTEGER PRIMARY KEY,
            triggered_at TEXT DEFAULT (datetime('now')),
            ticker TEXT,
            alert_type TEXT,        -- price_drop, news_signal, opportunity
            details TEXT,
            action_taken TEXT
        );
    """)

    # Seed current tax year
    current_year = datetime.now().year
    conn.execute(
        "INSERT OR IGNORE INTO tax_year (year, realized_gains_eur, exemption_used) VALUES (?, 22, 22)",
        (current_year,)
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")