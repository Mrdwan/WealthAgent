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
    ├── entrypoint.py      → init_db() → start dashboard subprocess → exec telegram_bot.py
    ├── telegram_bot.py    → bot handlers (commands, scheduled jobs)
    ├── db.py              → SQLite schema, Pydantic models, connection helpers
    ├── reports.py         → save/get/list/purge LLM reports, Ollama summary generation
    ├── fx_fetcher.py      → ECB daily FX rates → fx_rates table
    ├── price_fetcher.py   → Tiingo + yfinance prices → price_history table
    ├── fundamentals.py    → yfinance fundamentals → fundamentals table
    ├── news_fetcher.py    → RSS feeds → news_articles table
    ├── news_extractor.py  → Ollama extraction → news_signals table
    ├── alert_engine.py    → price/signal/opportunity checks → alerts_log table
    ├── notifier.py        → Telegram (or stdout) alert delivery
    ├── dashboard/         → FastAPI web dashboard (port 8080)
    │   ├── app.py         → FastAPI app factory, login/logout routes
    │   ├── auth.py        → single-user cookie auth (itsdangerous)
    │   ├── routes_reports.py  → GET /reports, GET /reports/{id}
    │   ├── routes_logs.py     → GET /logs, GET /logs/{filename}
    │   ├── routes_purge.py    → GET/POST /purge/logs, POST /purge/reports
    │   ├── routes_charts.py   → GET /charts, GET /api/charts/*
    │   ├── routes_alerts.py   → GET /alerts, GET/POST /alerts/config
    │   ├── templates/     → Jinja2 templates (base, login, reports, logs, charts, alerts, purge)
    │   └── static/        → vendored PicoCSS, HTMX, Chart.js, app.css
    └── config/
        └── settings.py    → pydantic-settings, all env vars
```

> `src/` files land at `/app/` root in the container — **not** `/app/src/`.
> `config/` lands at `/app/config/`.

---

## Telegram bot commands

| Command | Usage | Description |
|---------|-------|-------------|
| `/status` | `/status` | Portfolio summary |
| `/buy` | `/buy TICKER SHARES PRICE_EUR POOL` | Record a buy (pool: `long_term`, `short_term`, `bond`) |
| `/sell` | `/sell TICKER SHARES PRICE_EUR` | Record a sell (updates holdings automatically) |
| `/rebalance` | `/rebalance` | AI rebalance — sends summary + dashboard link |
| `/analyze` | `/analyze TICKER` | Deep analysis — sends summary + dashboard link |
| `/help` | `/help` | Show available commands |

Examples:
```
/buy AAPL 10 145.50 long_term
/sell AAPL 5 160.00
```

`/rebalance` and `/analyze` now send a 2–3 sentence Ollama-generated summary to Telegram
and save the full Claude report to the database, accessible via the dashboard.

---

## Web dashboard

FastAPI dashboard runs on port 8080 alongside the bot (started via `subprocess.Popen`
in `entrypoint.py` before `os.execv` to the bot).

| Route | Description |
|-------|-------------|
| `GET /` | Redirects to `/reports` |
| `GET /login` + `POST /login` | Single-user password auth (cookie session) |
| `GET /reports` | Paginated list of saved LLM reports |
| `GET /reports/{id}` | Full report rendered as markdown |
| `GET /logs` | List of `DD-MM-YYYY.log` files |
| `GET /logs/{filename}` | Line-numbered log file viewer |
| `GET /purge` | Purge controls UI |
| `POST /purge/logs` | Delete logs older than N days |
| `POST /purge/reports` | Delete expired reports immediately |
| `GET /charts` | Portfolio charts (value, P&L, allocation, tax year) |
| `GET /api/charts/*` | JSON data endpoints for Chart.js |
| `GET /alerts` | Recent alerts (last 30 days) |
| `GET /alerts/config` + `POST` | Configure alert thresholds (stored in `alert_config` table) |

### Dashboard design
- **Frontend:** HTMX + Jinja2 + PicoCSS (dark theme). Static assets vendored — no CDN, works offline.
- **Auth:** `itsdangerous.URLSafeTimedSerializer` signed cookie, 24h expiry, single password via `DASHBOARD_SECRET_KEY`.
- **No Node.js build step** — plain HTML/CSS/JS only.

---

## Design principles

### TDD — Test-Driven Development (mandatory)
All new code must follow the red-green-refactor cycle:
1. **Red:** write a failing test that defines the expected behaviour.
2. **Green:** write the minimal production code to make that test pass.
3. **Refactor:** clean up while keeping tests green.

Never write production code without a corresponding test first. This is
enforced by the 100% coverage gate in pre-commit, but the discipline goes
beyond coverage — tests drive the design.

### SRP — Single Responsibility Principle
Every module, class, and function should have **one reason to change**.

- `config/settings.py` is *declarative configuration only* — no loose parsing
  helpers, transformation logic, or utility functions.  If a field needs
  non-trivial parsing (e.g. JSON/comma-separated env vars), that logic
  belongs in a field validator or custom settings source within the module —
  not a standalone function unrelated to a field.
- Each `src/` module owns one pipeline stage (fetching, extraction, alerting,
  notification).  Don't mix concerns across modules.
- Functions should do one thing.  If a function name contains "and", split it.

### DRY — Don't Repeat Yourself
- Shared logic lives in a dedicated, well-named utility module — not
  copy-pasted across callers.
- Constants used in more than one module belong in `config/settings.py` (if
  they are user-configurable) or a `constants.py` module (if they are not).
- If you find yourself writing the same 3+ lines in two places, extract a
  helper.

### Keep modules focused
- No "grab-bag" modules.  Every module has a clear purpose stated in its
  docstring.
- Private helpers (`_func`) are fine *within* the module whose responsibility
  they serve.  They must not leak into unrelated modules.
- If a helper doesn't logically belong to its current module, move it to the
  module it serves or to a shared utility.

### Favour pure functions
- Prefer pure functions (input → output, no side effects) for
  transformation, parsing, and validation logic.
- Side-effectful code (I/O, DB writes, network calls) should be clearly
  separated from pure logic so both are independently testable.

---

## Key design rules

### Environment & settings
- All user-specific data (holdings, API keys, budget) comes from env vars — never hardcoded.
- `config/settings.py` uses `pydantic-settings` (`BaseSettings`) — always safe to
  import.  Env vars are read automatically; API keys are `str | None`; each
  consumer validates at point of use, not at import time.
- All modules read config from `settings` — no direct `os.environ` reads in
  source modules.
- `.env` files are loaded via `python-dotenv`.

### Database
- SQLite at `/app/data/wealthagent.db` (WAL mode, foreign keys ON).
- Use `get_conn()` for one-off queries; use `db_conn()` context manager for
  anything that mutates — it commits on success, rolls back on error.
- `init_db()` is idempotent (all DDL uses `IF NOT EXISTS`) — safe to call on
  every container start.
- `tickers` in `news_signals` is stored as a JSON string; use
  `NewsSignal.tickers_json()` to serialise before inserting.

### Code style & linting
- Python 3.12+ features (`X | Y` unions, `match`, etc.) are fine.
- Pydantic `BaseModel` for every data structure passed between modules.
- Type hints on every function signature and module-level variable.
- `pathlib.Path` for all file paths — no bare strings.
- Docstrings on all public functions and classes.
- No hardcoded personal data anywhere.
- **Ruff** enforces linting and formatting — config lives in `pyproject.toml`.
  - Lint rules: pycodestyle, pyflakes, isort, pep8-naming, pyupgrade, bugbear,
    simplify, pylint (convention/error/warning).
  - Line length: 100.
- **Pre-commit** runs ruff lint (`--fix`), ruff format, and pytest (100%
  coverage gate) on every commit.

### LLM usage pattern
- **Ollama (local):** news signal extraction, sentiment scoring, report summarization, cheap repeated inference.
  - Called via `/v1/chat/completions` with `response_format=json_schema` (extraction) or plain text (summaries).
  - Retries up to 3× with 10 s delay on connection errors (Pi may be slow to start).
  - `ExtractedSignal` in `news_extractor.py` is the extraction schema — separate from `db.NewsSignal`.
  - `reports.generate_summary()` uses Ollama to produce 2–3 sentence summaries of Claude reports.
- **Claude API (`claude-opus-4-6`):** deep analysis, trade thesis, final decisions.
- Never call Claude for tasks Ollama can handle adequately.

---

## Development workflow

### Package management — uv

This project uses **uv** for dependency management. Do not use `pip install`.

```bash
uv add <package>              # add dependency
uv add --group dev <package>  # add dev dependency
uv sync --dev                 # install all deps
uv lock                       # regenerate lockfile after editing pyproject.toml
```

`uv.lock` must be committed — the Dockerfile uses `uv sync --frozen` for
reproducible builds.

### Linting & formatting

```bash
uv run ruff check --fix src/ config/ tests/
uv run ruff format src/ config/ tests/
uv run pre-commit run --all-files   # run all hooks manually
```

Pre-commit hooks run automatically on `git commit`. To install after a fresh
clone: `uv run pre-commit install`.

### Testing & coverage

Two kinds of tests:
- **Unit tests** (`test_*.py`, one per src module) — mock all external deps.
  Run fast, no network needed.
- **Integration tests** (`test_fetchers.py`, `test_news_pipeline.py`) — hit
  live APIs; run inside Docker or with real credentials.

`conftest.py` bootstraps all tests: creates a temp SQLite DB, stubs required
env vars, and provides an `autouse` fixture that cleans all tables between tests.

Run tests inside Docker (the container entrypoint starts the bot, so override it):

```bash
# Default — Ollama integration tests skipped
docker compose run --rm --entrypoint "" wealthagent python -m pytest --cov --cov-branch --cov-report=term-missing

# Full run including live Ollama tests
docker compose run --rm --entrypoint "" wealthagent python -m pytest --with-ollama
```

Tests marked `@pytest.mark.ollama` require a live Ollama instance and are skipped by default.

Coverage target: **100% line and branch** (`fail_under = 100` in `pyproject.toml`).
Excluded: `if __name__` blocks, `TYPE_CHECKING` guards, `# pragma: no cover`.
When adding new source code, add corresponding unit tests to maintain 100%.

### Docker

Docker-first project for runtime. The Dockerfile installs all deps (including dev)
from the lockfile.

```bash
docker compose up --build                # build and start
docker compose logs -f wealthagent       # tail logs
docker compose exec wealthagent bash     # shell into container
```

Run modules in the container: `docker compose exec wealthagent python -m <module>`
(e.g. `fx_fetcher`, `price_fetcher`, `news_fetcher`, `alert_engine`).

---

## Environment setup

```bash
cp .env.example .env
# Fill in: TIINGO_API_KEY, ADVISOR_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Dashboard: DASHBOARD_SECRET_KEY, DASHBOARD_BASE_URL
```

All other variables have sensible defaults — see `.env.example`.

### Dashboard-specific env vars

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DASHBOARD_SECRET_KEY` | Yes | — | Login password for the dashboard |
| `DASHBOARD_BASE_URL` | Recommended | `http://localhost:8080` | Full URL used in Telegram links (e.g. `http://192.168.1.x:8080`) |
| `DASHBOARD_ENABLED` | No | `true` | Set to `false` to disable the dashboard |
| `DASHBOARD_PORT` | No | `8080` | Port the dashboard listens on |
| `REPORT_RETENTION_DAYS` | No | `90` | Days before reports are auto-purged |
