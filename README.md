# WealthAgent

> Personal investment pipeline for a Raspberry Pi 5 — local LLM for fast inference, Claude for deep analysis, all surfaced through Telegram.

---

## How it works

```
News & Prices → Ollama (extract signals) → Alert Engine → Telegram Bot
                                                ↓
                                         Claude (deep analysis / rebalance)
```

1. **Hourly** — fetches prices and scans news RSS feeds
2. **Daily** — runs FX rates, fundamentals, alert checks
3. **Weekly** — screener finds new candidates
4. **Monthly** — Claude rebalances the portfolio
5. **On demand** — Telegram commands any time

---

## Stack

| Layer | Tool |
|---|---|
| Runtime | Docker on Raspberry Pi 5 |
| Local LLM | Ollama (`gemma4:e4b`) |
| Advisor LLM | Claude via API |
| Price data | Tiingo + yfinance |
| FX rates | ECB daily feed |
| News | RSS feeds |
| Database | SQLite (WAL mode) |
| Interface | Telegram bot |

---

## Quick Start

**1. Clone and configure**
```bash
git clone <repo>
cd WealthAgent
cp .env.example .env
```

**2. Fill in required variables in `.env`**
```bash
TIINGO_API_KEY=           # tiingo.com
ADVISOR_MODEL=claude-opus-4-6
ADVISOR_API_KEY=          # Anthropic API key
ADVISOR_BASE_URL=https://api.anthropic.com/v1
TELEGRAM_BOT_TOKEN=       # from @BotFather
TELEGRAM_CHAT_ID=         # your chat ID from @userinfobot
```

**3. Start**
```bash
docker compose up --build
```

---

## Telegram Commands

| Command | Example | What it does |
|---|---|---|
| `/status` | `/status` | Portfolio summary with P&L |
| `/buy` | `/buy AAPL 10 145.50 long_term` | Record a buy |
| `/sell` | `/sell AAPL 5 160.00` | Record a sell |
| `/analyze` | `/analyze NVDA` | Deep analysis via Claude (~30s) |
| `/rebalance` | `/rebalance` | Monthly rebalance via Claude (~30s) |
| `/help` | `/help` | Show all commands |

> Pool options for `/buy`: `long_term` · `short_term` · `bond`

---

## Architecture

```
docker-compose
├── ollama              ← local LLM server
└── wealthagent
    ├── entrypoint.py   ← init DB → validate env → start bot
    ├── telegram_bot.py ← bot commands + scheduler
    ├── db.py           ← SQLite schema + Pydantic models
    ├── advisor.py      ← Claude-powered analysis & rebalancing
    ├── alert_engine.py ← price drop / signal / opportunity alerts
    ├── screener.py     ← finds new stock candidates
    ├── news_extractor.py ← Ollama signal extraction from RSS
    ├── price_fetcher.py  ← Tiingo + yfinance
    ├── fundamentals.py   ← yfinance fundamentals
    ├── fx_fetcher.py     ← ECB daily FX rates
    └── notifier.py       ← Telegram message delivery
```

---

## Portfolio Configuration

Key settings in `.env`:

```bash
MONTHLY_BUDGET_EUR=2000   # total monthly investment budget
LONG_TERM_PCT=0.75        # 75% to long-term pool
SHORT_TERM_PCT=0.25       # 25% to short-term pool
CGT_RATE=0.33             # capital gains tax rate
ANNUAL_EXEMPTION=1270     # annual CGT exemption (EUR)
ALERT_DROP_PCT=10         # alert when holding drops 10%+
STOP_LOSS_PCT=8           # exit short-term at -8%
```

---

## Development

```bash
# Install deps
uv sync --dev

# Run tests
docker compose run --rm --entrypoint "" wealthagent \
  python -m pytest --cov --cov-branch --cov-report=term-missing

# Lint
uv run ruff check --fix src/ config/ tests/
```

> 100% test coverage enforced on every commit via pre-commit hooks.

---

## Alert Types

| Alert | Trigger |
|---|---|
| `price_drop` | Holding drops >10% in 30 days |
| `news_signal` | Negative signal on held ticker (confidence ≥ 0.6) |
| `opportunity` | Positive signal on unwatched ticker (confidence ≥ 0.7) |
