# WealthAgent

> Personal investment pipeline for a Raspberry Pi 5 — local LLM for fast inference, Claude for deep analysis, surfaced through Telegram and a web dashboard.

---

## How it works

```
News & Prices → Ollama (extract signals) → Alert Engine → Telegram Bot
                                                ↓
                                         Claude (deep analysis / rebalance)
                                                ↓
                                    Ollama (summarise) → Telegram summary + Dashboard link
```

1. **Hourly** — fetches prices and scans news RSS feeds
2. **Daily** — runs FX rates, fundamentals, alert checks, purges expired reports
3. **Weekly** — screener finds new candidates
4. **Monthly** — Claude rebalances the portfolio
5. **On demand** — Telegram commands any time
6. **Always on** — web dashboard on port 8080

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
| Bot interface | Telegram |
| Web dashboard | FastAPI + HTMX + PicoCSS + Chart.js |

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

# Dashboard
DASHBOARD_SECRET_KEY=     # password to log into the dashboard
DASHBOARD_BASE_URL=       # e.g. http://192.168.1.x:8080  (used in Telegram links)
```

**3. Start**
```bash
docker compose up --build
```

**4. Open the dashboard**

Navigate to `http://<pi-ip>:8080` and log in with your `DASHBOARD_SECRET_KEY`.

---

## Telegram Commands

| Command | Example | What it does |
|---|---|---|
| `/status` | `/status` | Portfolio summary with P&L |
| `/buy` | `/buy AAPL 10 145.50 long_term` | Record a buy |
| `/sell` | `/sell AAPL 5 160.00` | Record a sell |
| `/analyze` | `/analyze NVDA` | Deep analysis via Claude — sends summary + link |
| `/rebalance` | `/rebalance` | Monthly rebalance via Claude — sends summary + link |
| `/help` | `/help` | Show all commands |

> Pool options for `/buy`: `long_term` · `short_term` · `bond`

`/rebalance` and `/analyze` send a short Ollama-generated summary to Telegram with a link
to the full report on the dashboard.

---

## Web Dashboard

Runs on port 8080 alongside the bot. Single-user, password-protected.

| Page | URL | Description |
|------|-----|-------------|
| Reports | `/reports` | List of all saved LLM reports |
| Report detail | `/reports/{id}` | Full markdown-rendered report |
| Logs | `/logs` | List of daily log files |
| Log viewer | `/logs/{filename}` | Line-numbered log content |
| Charts | `/charts` | Portfolio charts (see below) |
| Alerts | `/alerts` | Recent alerts (last 30 days) |
| Alert config | `/alerts/config` | Configure price-drop and stop-loss thresholds |
| Purge | `/purge` | Delete old logs / expired reports |

### Charts included

| Chart | Type | Description |
|-------|------|-------------|
| Portfolio value over time | Line | Daily value, last 90 days |
| Unrealized P&L by ticker | Bar | Green/red per position |
| Allocation by pool | Pie | Long-term / Short-term / Bond split |
| Tax year | Bar | Realized gains vs exemption used vs remaining |

---

## Architecture

```
docker-compose
├── ollama              ← local LLM server
└── wealthagent
    ├── entrypoint.py   ← init DB → start dashboard → exec bot
    ├── telegram_bot.py ← bot commands + scheduler
    ├── db.py           ← SQLite schema + Pydantic models
    ├── reports.py      ← save/retrieve LLM reports, Ollama summarisation
    ├── advisor.py      ← Claude-powered analysis & rebalancing
    ├── alert_engine.py ← price drop / signal / opportunity alerts
    ├── screener.py     ← finds new stock candidates
    ├── news_extractor.py ← Ollama signal extraction from RSS
    ├── price_fetcher.py  ← Tiingo + yfinance
    ├── fundamentals.py   ← yfinance fundamentals
    ├── fx_fetcher.py     ← ECB daily FX rates
    ├── notifier.py       ← Telegram message delivery
    └── dashboard/        ← FastAPI web dashboard
        ├── app.py        ← app factory, auth routes
        ├── auth.py       ← cookie session (itsdangerous)
        ├── routes_*.py   ← reports, logs, charts, alerts, purge
        ├── templates/    ← Jinja2 HTML templates
        └── static/       ← PicoCSS, HTMX, Chart.js (vendored)
```

---

## Portfolio Configuration

Key settings in `.env`:

```bash
MONTHLY_BUDGET_EUR=2000    # total monthly investment budget
LONG_TERM_PCT=0.75         # 75% to long-term pool
SHORT_TERM_PCT=0.25        # 25% to short-term pool
CGT_RATE=0.33              # capital gains tax rate
ANNUAL_EXEMPTION=1270      # annual CGT exemption (EUR)
ALERT_DROP_PCT=10          # alert when holding drops 10%+
STOP_LOSS_PCT=8            # exit short-term at -8%
REPORT_RETENTION_DAYS=90   # days before reports are auto-purged
```

Alert thresholds (`ALERT_DROP_PCT`, `STOP_LOSS_PCT`, `DIVIDEND_YIELD_MAX`) can also be
overridden at runtime via the dashboard at `/alerts/config`.

---

## Development

```bash
# Install deps
uv sync --dev

# Run tests (local)
uv run pytest --cov --cov-branch --cov-report=term-missing

# Run tests (in Docker, matching production environment)
docker compose run --rm --entrypoint "" wealthagent \
  python -m pytest --cov --cov-branch --cov-report=term-missing

# Lint
uv run ruff check --fix src/ config/ tests/
uv run ruff format src/ config/ tests/
```

> 100% test coverage enforced on every commit via pre-commit hooks.

---

## Alert Types

| Alert | Trigger |
|---|---|
| `price_drop` | Holding drops >10% in 30 days |
| `news_signal` | Negative signal on held ticker (confidence ≥ 0.6) |
| `opportunity` | Positive signal on unwatched ticker (confidence ≥ 0.7) |
