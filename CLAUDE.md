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
│   └── fundamentals.py
├── config/
│   ├── __init__.py
│   └── settings.py
├── tests/              # integration tests — copied to /app/tests/ in the container
│   └── test_fetchers.py
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

### LLM usage pattern (planned)
- **Ollama (local):** screening, sentiment scoring, cheap repeated inference.
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

To run integration tests (hits live APIs):
```bash
docker compose exec wealthagent python tests/test_fetchers.py
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

### Import pattern
- Like `db.py`, fetchers read `TIINGO_API_KEY` from `os.environ` directly —
  they do **not** import `config.settings`, so they can run standalone.
- `price_fetcher` imports from `fx_fetcher` (for EUR conversion).
- `fundamentals` imports only from `db`.
