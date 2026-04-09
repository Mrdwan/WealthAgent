# WealthAgent — CLAUDE.md

Investment pipeline designed to run on a Raspberry Pi 5.
Combines a local LLM (Ollama) for cheap/fast inference with Claude API for
deep analysis, surfaced through a Telegram bot.

---

## Architecture

```
docker-compose
├── ollama          # local LLM server (gemma4:e4b default)
└── wealthagent
    ├── entrypoint.py      → init_db() → validate settings → exec telegram_bot.py
    ├── telegram_bot.py    → bot handlers (commands, scheduled jobs)
    ├── db.py              → SQLite schema, Pydantic models, connection helpers
    ├── fx_fetcher.py      → ECB daily FX rates → fx_rates table
    ├── price_fetcher.py   → Tiingo + yfinance prices → price_history table
    ├── fundamentals.py    → yfinance fundamentals → fundamentals table
    ├── news_fetcher.py    → RSS feeds → news_articles table
    ├── news_extractor.py  → Ollama extraction → news_signals table
    ├── alert_engine.py    → price/signal/opportunity checks → alerts_log table
    ├── notifier.py        → Telegram (or stdout) alert delivery
    └── config/
        └── settings.py    → pydantic-settings, all env vars
```

**Data flow:**
1. Scheduled jobs fetch FX rates (ECB), prices (Tiingo/yfinance), fundamentals (yfinance), RSS news
2. Local Ollama screens news and scores screener candidates (cheap pass)
3. Claude API performs deeper analysis and generates trade thesis
4. Telegram bot delivers alerts and accepts commands

---

## Project layout

```
/
├── src/                # Python source — copied to /app/ in the container
│   ├── db.py
│   ├── entrypoint.py
│   ├── telegram_bot.py
│   ├── fx_fetcher.py
│   ├── price_fetcher.py
│   ├── fundamentals.py
│   ├── news_fetcher.py
│   ├── news_extractor.py
│   ├── alert_engine.py
│   └── notifier.py
├── config/
│   ├── __init__.py
│   └── settings.py
├── tests/              # integration tests — copied to /app/tests/ in the container
│   ├── test_fetchers.py
│   └── test_news_pipeline.py
├── data/               # mounted volume — SQLite DB lives here (gitignored)
├── logs/               # mounted volume (gitignored)
├── Dockerfile
├── docker-compose.yaml
└── .env.example
```

> `src/` files land at `/app/` root in the container — **not** `/app/src/`.
> `config/` lands at `/app/config/`.

---

## Key design rules

### Environment & settings
- All user-specific data (holdings, API keys, budget) comes from env vars — never hardcoded.
- `config/settings.py` validates required vars **on import** and raises a clear
  `EnvironmentError` listing what is missing.
- `db.py` reads `DB_PATH` directly from `os.environ` — it does **not** import
  `config.settings` — so `init_db()` runs without API keys.
- Required vars: `TIINGO_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### Database
- SQLite at `/app/data/wealthagent.db` (WAL mode, foreign keys ON).
- Use `get_conn()` for one-off queries; use `db_conn()` context manager for
  anything that mutates — it commits on success, rolls back on error.
- `init_db()` is idempotent (all DDL uses `IF NOT EXISTS`) — safe to call on
  every container start.
- `tickers` in `news_signals` is stored as a JSON string; use
  `NewsSignal.tickers_json()` to serialise before inserting.

### Code style
- Python 3.12+ features (`X | Y` unions, `match`, etc.) are fine.
- Pydantic `BaseModel` for every data structure passed between modules.
- Type hints on every function signature and module-level variable.
- `pathlib.Path` for all file paths — no bare strings.
- Docstrings on all public functions and classes.
- No hardcoded personal data anywhere.

### LLM usage pattern
- **Ollama (local):** news signal extraction, sentiment scoring, cheap repeated inference.
  - Called via `/v1/chat/completions` with `response_format=json_schema` for guaranteed valid JSON.
  - Retries up to 3 times with 10 s delay on connection errors (Pi may be slow to start).
  - `ExtractedSignal` in `news_extractor.py` is the extraction schema — separate from `db.NewsSignal`.
- **Claude API (`claude-opus-4-6`):** deep analysis, trade thesis, final decisions.
- Never call Claude for tasks Ollama can handle adequately.

---

## Development workflow

This is a **Docker-first** project. Do not run `pip install` or test modules
locally — all dependencies are installed in the image.

```bash
# Build and start everything
docker compose up --build

# Tail logs
docker compose logs -f wealthagent

# Open a shell in the running container
docker compose exec wealthagent bash

# Rebuild after Dockerfile changes
docker compose up --build --force-recreate

# Inspect the database
docker compose exec wealthagent sqlite3 /app/data/wealthagent.db ".tables"
```

To test DB init in isolation:
```bash
docker compose run --rm wealthagent python db.py
```

To run individual fetchers:
```bash
docker compose exec wealthagent python -m fx_fetcher
docker compose exec wealthagent python -m price_fetcher
docker compose exec wealthagent python -m fundamentals
```

To run the news pipeline manually:
```bash
# 1. Fetch new articles from RSS feeds
docker compose exec wealthagent python -m news_fetcher

# 2. Extract signals from unprocessed articles (fast, single pass)
docker compose exec wealthagent python -m news_extractor

# 3. Check for alerts and log them
docker compose exec wealthagent python -m alert_engine

# 4. Send a test notification
docker compose exec wealthagent python -m notifier "WealthAgent test message"
```

To run integration tests (hits live APIs):
```bash
docker compose exec wealthagent python tests/test_fetchers.py
```

To run news pipeline tests (Ollama tests auto-skip if not reachable):
```bash
docker compose exec wealthagent python tests/test_news_pipeline.py
```

---

## Environment setup

```bash
cp .env.example .env
# Fill in at minimum:
#   TIINGO_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

All other variables have sensible defaults — see `.env.example` for the full
list and comments.

---

## Database tables (quick reference)

| Table                 | Purpose                                      |
|-----------------------|----------------------------------------------|
| `holdings`            | Current portfolio positions                  |
| `price_history`       | Daily OHLCV per ticker (Tiingo)              |
| `fx_rates`            | Daily EUR/USD and other FX rates             |
| `fundamentals`        | P/E, revenue growth, etc. per ticker         |
| `news_articles`       | Raw articles from RSS feeds                  |
| `news_signals`        | LLM-extracted signals from articles          |
| `trades`              | Buy/sell transaction log                     |
| `tax_year`            | Annual CGT tracking                          |
| `screener_candidates` | Stocks flagged for potential addition        |
| `alerts_log`          | History of triggered price/signal alerts     |

Pool values: `long_term` · `short_term` · `bond`
Trade actions: `buy` · `sell`
Screener status: `pending` · `reviewed` · `added` · `rejected`

---

## Data fetchers

### FX rates (`fx_fetcher.py`)
- Source: ECB daily XML feed (free, no API key).
- Stores all EUR-based pairs (EURUSD, EURGBP, etc.) in `fx_rates`.
- `get_rate_for_date(pair, date)` handles weekends/holidays by returning the
  most recent prior rate.
- Conversion helpers: `usd_to_eur()`, `gbp_to_eur()` — accept an optional
  date to use the historical rate.

### Prices (`price_fetcher.py`)
- **Tiingo** (primary, US equities) — requires `TIINGO_API_KEY`, uses IEX endpoint.
- **yfinance** (fallback for all tickers, primary for commodities) — free, no key.
- If Tiingo fails for a ticker, falls back to yfinance automatically with a log warning.
- Commodities use `_COMMODITY_MAP` for ticker translation (e.g. `XAG` → `SI=F`).
- `fetch_all_prices()` reads holdings, fetches ECB rates first, then converts
  each price to EUR using the **same-day FX rate** (critical for Irish CGT).
- Network timeouts: 15 s for Tiingo, yfinance manages its own.

### Fundamentals (`fundamentals.py`)
- Source: yfinance `Ticker.info` and `Ticker.calendar`.
- Stores structured fields + full `raw_json` for future use.
- Skips bonds and commodities (defined in `_SKIP_TICKERS`).
- yfinance is flaky — failures are logged and skipped, never crash the run.

### News pipeline (`news_fetcher.py`, `news_extractor.py`)
- `news_fetcher` reads RSS feeds via `feedparser`, deduplicates by URL, stores up to 20
  articles per feed per run in `news_articles`.
- `news_extractor` calls Ollama `/v1/chat/completions` with `response_format=json_schema`.
  - `call_ollama(text)` → `ExtractedSignal` (Pydantic model with Literal-typed fields).
  - `score_confidence(text)` → runs 3× at temps 0.1/0.3/0.5; returns `(signal, 0.9|0.6|0.3)`.
  - `process_unprocessed(use_confidence_scoring)` → batch-processes all `processed=0` articles;
    logs per-article timing; never crashes on a single article failure.
  - Fallback JSON parser handles plain JSON, markdown code blocks, and prose-embedded JSON.

### Alert engine (`alert_engine.py`)
- `check_price_drops(threshold_pct)` — compares current vs 30-day-ago EUR price; default
  threshold from `ALERT_DROP_PCT` env var (default 10%).
- `check_news_signals(hours)` — negative signals with `confidence >= 0.6` for held tickers.
- `check_opportunities(hours)` — positive signals with `confidence >= 0.7` for non-held tickers.
- `run_all_checks()` — runs all three, deduplicates on `(type, ticker)`, logs to `alerts_log`.
- `Alert` Pydantic model: `type`, `ticker`, `details` (JSON-serializable dict), `triggered_at`.

### Notifier (`notifier.py`)
- `send_message(text)` — POSTs to Telegram Bot API; falls back to stdout if
  `TELEGRAM_BOT_TOKEN` is not set (safe for development).
- `send_alert(alert)` — formats an `Alert` with type-specific layout and emoji, then calls
  `send_message`. Splits messages exceeding Telegram's 4096-char limit on newlines.

### Import pattern
- Like `db.py`, fetchers read `TIINGO_API_KEY` from `os.environ` directly —
  they do **not** import `config.settings`, so they can run standalone.
- `price_fetcher` imports from `fx_fetcher` (for EUR conversion).
- `fundamentals` imports only from `db`.
- `news_fetcher` and `news_extractor` read `OLLAMA_BASE_URL` / `OLLAMA_MODEL` from
  `os.environ` directly; `news_fetcher` lazily imports `config.settings` for RSS feeds
  and falls back to hardcoded defaults if API keys are absent.
- `notifier` imports `Alert` from `alert_engine`; reads `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID` from `os.environ` directly.
