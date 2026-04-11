# WealthAgent Dashboard — Implementation Plan

## Overview

A web dashboard for WealthAgent that provides:
- Full LLM reports (with short summaries sent to Telegram)
- Log file viewer
- Purge controls
- Investment charts and graphs
- Alerts management

**Tech Stack:** FastAPI + HTMX + Jinja2 + PicoCSS + Chart.js

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Container                              │
│                                                                  │
│  ┌──────────────┐      ┌────────────────────────────────────┐  │
│  │ Telegram Bot │      │       FastAPI Dashboard             │  │
│  │              │      │  HTMX + Jinja2 + PicoCSS            │  │
│  │ /rebalance ──────▶  │  Port 8080                          │  │
│  │ /analyze ────────▶  │                                     │  │
│  └──────────────┘      └──────────────────┬─────────────────┘  │
│         │                                  │                    │
│         ▼                                  ▼                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                      SQLite DB                           │   │
│  │  holdings | reports (NEW) | alerts_log | alert_config    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## New Dependencies

```toml
# Add to pyproject.toml
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
jinja2>=3.1.0
python-multipart>=0.0.20
itsdangerous>=2.2.0
markdown>=3.7
```

---

## New Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DASHBOARD_ENABLED` | bool | `true` | Enable/disable dashboard |
| `DASHBOARD_PORT` | int | `8080` | Dashboard port |
| `DASHBOARD_SECRET_KEY` | str | required | Password for login |
| `DASHBOARD_BASE_URL` | str | `None` | URL for Telegram links (e.g., `http://192.168.1.x:8080`) |
| `REPORT_RETENTION_DAYS` | int | `90` | Auto-purge reports after N days |

---

## Database Schema Changes

### New Table: `reports`

```sql
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
```

### New Table: `alert_config` (Phase 5)

```sql
CREATE TABLE IF NOT EXISTS alert_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

---

## File Structure

```
src/
├── dashboard/
│   ├── __init__.py
│   ├── app.py              # FastAPI app factory, main routes
│   ├── auth.py             # Authentication middleware, login/logout
│   ├── routes_reports.py   # Report list/detail routes
│   ├── routes_logs.py      # Log viewer routes
│   ├── routes_purge.py     # Purge control routes
│   ├── routes_charts.py    # Chart data API routes
│   ├── routes_alerts.py    # Alerts list/config routes
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── reports/
│   │   │   ├── list.html
│   │   │   └── detail.html
│   │   ├── logs/
│   │   │   ├── list.html
│   │   │   └── view.html
│   │   ├── purge.html
│   │   ├── charts.html
│   │   └── alerts/
│   │       ├── list.html
│   │       └── config.html
│   └── static/
│       ├── pico.min.css
│       ├── htmx.min.js
│       ├── chart.min.js
│       └── app.css
├── reports.py              # Report storage, retrieval, summarization
└── ... (existing files)

tests/
├── test_reports.py
├── test_dashboard_app.py
├── test_dashboard_auth.py
├── test_dashboard_logs.py
├── test_dashboard_purge.py
├── test_dashboard_charts.py
├── test_dashboard_alerts.py
└── ... (existing files)
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Redirect to /login or /reports |
| GET | `/login` | No | Login page |
| POST | `/login` | No | Authenticate, set session cookie |
| GET | `/logout` | Yes | Clear session, redirect to login |
| GET | `/reports` | Yes | List reports (paginated) |
| GET | `/reports/{id}` | Yes | View single report |
| GET | `/logs` | Yes | List log files |
| GET | `/logs/{filename}` | Yes | View log file contents |
| GET | `/purge` | Yes | Purge controls page |
| POST | `/purge/logs` | Yes | Execute log purge |
| POST | `/purge/reports` | Yes | Execute expired reports purge |
| GET | `/charts` | Yes | Charts dashboard page |
| GET | `/api/charts/portfolio-value` | Yes | JSON: portfolio value over time |
| GET | `/api/charts/pnl-by-ticker` | Yes | JSON: P&L by ticker |
| GET | `/api/charts/allocation` | Yes | JSON: allocation by pool |
| GET | `/api/charts/tax-year` | Yes | JSON: tax gains vs exemption |
| GET | `/alerts` | Yes | Alerts list page |
| GET | `/alerts/config` | Yes | Alert configuration page |
| POST | `/alerts/config` | Yes | Update alert thresholds |

---

## Phase 1: Reports + Telegram Summary

**Goal:** Save full LLM reports to database, send short summary to Telegram with dashboard link.

### Tasks

- [x] **1.1** Add dependencies to `pyproject.toml`
  - fastapi, uvicorn, jinja2, python-multipart, itsdangerous, markdown
  - Run `uv lock` and `uv sync`

- [x] **1.2** Add dashboard settings to `config/settings.py`
  - `dashboard_enabled: bool = True`
  - `dashboard_port: int = 8080`
  - `dashboard_secret_key: str | None = None`
  - `dashboard_base_url: str | None = None`
  - `report_retention_days: int = 90`
  - Write tests for new settings

- [x] **1.3** Add `reports` table to `db.py`
  - Add DDL to `_SCHEMA`
  - Add `Report` Pydantic model
  - Test `init_db()` creates table idempotently

- [x] **1.4** Create `src/reports.py` module
  - [x] Write tests first (TDD)
  - [x] `save_report(report_type, ticker, summary, full_content) -> int`
  - [x] `get_report(report_id) -> Report | None`
  - [x] `list_reports(limit=20, offset=0) -> list[Report]`
  - [x] `count_reports() -> int`
  - [x] `purge_expired_reports() -> int` (returns count deleted)
  - [x] `generate_summary(full_content) -> str` (uses Ollama)

- [x] **1.5** Create `src/dashboard/__init__.py`
  - Empty file for package

- [x] **1.6** Create `src/dashboard/auth.py`
  - [x] Write tests first
  - [x] `verify_password(password) -> bool`
  - [x] `create_session_token() -> str`
  - [x] `verify_session_token(token) -> bool`
  - [x] `login_required` dependency for FastAPI

- [x] **1.7** Create `src/dashboard/app.py`
  - [x] Write tests first
  - [x] FastAPI app factory `create_app() -> FastAPI`
  - [x] Mount static files
  - [x] Setup Jinja2 templates
  - [x] Routes: `GET /`, `GET /login`, `POST /login`, `GET /logout`
  - [x] `run_dashboard()` function to start uvicorn

- [x] **1.8** Create `src/dashboard/routes_reports.py`
  - [x] Write tests first
  - [x] `GET /reports` — list with pagination
  - [x] `GET /reports/{id}` — detail view

- [x] **1.9** Create base template `src/dashboard/templates/base.html`
  - Navigation: Reports, Logs, Charts, Alerts, Purge, Logout
  - Include PicoCSS, HTMX
  - Mobile-friendly viewport

- [x] **1.10** Create `src/dashboard/templates/login.html`
  - Password input form
  - Error message display

- [x] **1.11** Create `src/dashboard/templates/reports/list.html`
  - Table: date, type, ticker, summary (truncated)
  - Pagination controls
  - Link to detail

- [x] **1.12** Create `src/dashboard/templates/reports/detail.html`
  - Full report content (rendered as markdown)
  - Back to list link
  - Report metadata (date, type, ticker)

- [x] **1.13** Download and add static files
  - `src/dashboard/static/pico.min.css`
  - `src/dashboard/static/htmx.min.js`
  - `src/dashboard/static/app.css` (minimal custom styles)

- [x] **1.14** Update `telegram_bot.py` — integrate reports
  - [x] Write tests first
  - [x] Modify `cmd_rebalance()`:
    - Call `monthly_rebalance()`
    - Call `generate_summary()` via Ollama
    - Call `save_report()`
    - Send summary + link to Telegram
  - [x] Modify `cmd_analyze()` similarly

- [x] **1.15** Update `entrypoint.py` — start dashboard
  - [x] Write tests first
  - [x] If `dashboard_enabled`, start dashboard in thread/subprocess
  - [x] Log dashboard URL on startup

- [x] **1.16** Update `docker-compose.yaml`
  - Expose port 8080
  - Add `DASHBOARD_SECRET_KEY` to env

- [x] **1.17** Add scheduled task to purge expired reports
  - Run `purge_expired_reports()` daily in telegram_bot.py scheduler

- [x] **1.18** Integration test: full flow
  - Login → view reports → verify content

---

## Phase 2: Logs Viewer

**Goal:** Simple log file browser — list files, view contents.

### Tasks

- [x] **2.1** Create `src/dashboard/routes_logs.py`
  - [x] Write tests first
  - [x] `GET /logs` — list log files sorted by date desc
  - [x] `GET /logs/{filename}` — view file contents
  - [x] Validate filename format (DD-MM-YYYY.log)
  - [x] Escape HTML in log contents

- [x] **2.2** Create `src/dashboard/templates/logs/list.html`
  - List of log files as links
  - Show file size and date

- [x] **2.3** Create `src/dashboard/templates/logs/view.html`
  - Pre-formatted log content
  - Line numbers
  - Back to list link

- [x] **2.4** Add "Logs" link to navigation in base.html

---

## Phase 3: Purge Controls

**Goal:** UI to delete old logs.

### Tasks

- [x] **3.1** Create `src/dashboard/routes_purge.py`
  - [x] Write tests first
  - [x] `GET /purge` — purge controls page
  - [x] `POST /purge/logs` — delete logs older than X days
  - [x] `POST /purge/reports` — delete expired reports manually
  - [x] Return count of deleted items

- [x] **3.2** Create `src/dashboard/templates/purge.html`
  - Form: "Delete logs older than [ ] days" + submit
  - Form: "Delete expired reports now" + submit
  - Show result message after purge

- [x] **3.3** Add "Purge" link to navigation in base.html

---

## Phase 4: Charts & Graphs

**Goal:** Visualize portfolio data with useful charts.

### Tasks

- [ ] **4.1** Download Chart.js
  - `src/dashboard/static/chart.min.js`

- [ ] **4.2** Create `src/dashboard/routes_charts.py`
  - [ ] Write tests first
  - [ ] `GET /charts` — charts dashboard page
  - [ ] `GET /api/charts/portfolio-value` — JSON data
  - [ ] `GET /api/charts/pnl-by-ticker` — JSON data
  - [ ] `GET /api/charts/allocation` — JSON data
  - [ ] `GET /api/charts/tax-year` — JSON data

- [ ] **4.3** Implement `portfolio_value_data()` helper
  - Query price_history + holdings
  - Compute daily portfolio value
  - Return last 90 days by default

- [ ] **4.4** Implement `pnl_by_ticker_data()` helper
  - Compute unrealized P&L per ticker
  - Return sorted by P&L amount

- [ ] **4.5** Implement `allocation_data()` helper
  - Group holdings by pool
  - Return value per pool

- [ ] **4.6** Implement `tax_year_data()` helper
  - Query tax_year table
  - Return realized gains, exemption used, remaining

- [ ] **4.7** Create `src/dashboard/templates/charts.html`
  - 2x2 grid of chart cards
  - Each card: title + canvas element
  - JavaScript to fetch data and render Chart.js

- [ ] **4.8** Add "Charts" link to navigation in base.html

---

## Phase 5: Alerts Dashboard

**Goal:** View alerts and configure thresholds.

### Tasks

- [ ] **5.1** Add `alert_config` table to `db.py`
  - Simple key-value store for thresholds

- [ ] **5.2** Create `src/dashboard/routes_alerts.py`
  - [ ] Write tests first
  - [ ] `GET /alerts` — list recent alerts
  - [ ] `GET /alerts/config` — configuration page
  - [ ] `POST /alerts/config` — update thresholds

- [ ] **5.3** Implement alert config helpers
  - `get_alert_config(key) -> str | None`
  - `set_alert_config(key, value) -> None`
  - `list_alert_configs() -> dict[str, str]`

- [ ] **5.4** Create `src/dashboard/templates/alerts/list.html`
  - Table: timestamp, type, ticker, details
  - Filter by alert type (optional)
  - Link to config

- [ ] **5.5** Create `src/dashboard/templates/alerts/config.html`
  - Form with threshold inputs:
    - `price_drop_pct` (default 10%)
    - `price_spike_pct` (default 15%)
    - Other thresholds from alert_engine.py
  - Save button

- [ ] **5.6** Update `alert_engine.py` to read thresholds from DB
  - Fall back to settings/defaults if not configured

- [ ] **5.7** Add "Alerts" link to navigation in base.html

---

## Testing Checklist

All tests must pass with 100% coverage before each phase is complete.

### Phase 1 Tests
- [x] `tests/test_reports.py` — all report functions
- [x] `tests/test_dashboard_app.py` — app factory, login, logout
- [x] `tests/test_dashboard_auth.py` — auth functions
- [x] `tests/test_dashboard_reports.py` — report routes

### Phase 2 Tests
- [x] `tests/test_dashboard_logs.py` — log routes

### Phase 3 Tests
- [x] `tests/test_dashboard_purge.py` — purge routes

### Phase 4 Tests
- [ ] `tests/test_dashboard_charts.py` — chart routes and data helpers

### Phase 5 Tests
- [ ] `tests/test_dashboard_alerts.py` — alert routes and config

---

## Telegram Message Format

After `/rebalance`:
```
Portfolio is healthy. Consider trimming NVDA (+45%) to lock gains.
Maintain MSFT and AAPL positions.

Full report: http://192.168.1.x:8080/reports/42
```

After `/analyze PLTR`:
```
PLTR shows strong growth but high valuation. Consider small position
in long_term pool if price drops 10%.

Full report: http://192.168.1.x:8080/reports/43
```

---

## Environment Variables (.env additions)

```bash
# Dashboard
DASHBOARD_ENABLED=true
DASHBOARD_PORT=8080
DASHBOARD_SECRET_KEY=your-secret-password-here
DASHBOARD_BASE_URL=http://192.168.1.x:8080

# Report retention
REPORT_RETENTION_DAYS=90
```

---

## Completion Criteria

Each phase is complete when:
1. All tasks are checked off
2. All tests pass (`pytest --cov --cov-branch`)
3. 100% code coverage maintained
4. Docker build succeeds
5. Manual testing in browser confirms functionality
