"""Application settings for WealthAgent.

All values are read from environment variables (or a .env file).

Module-level constants (DB_PATH, LOG_DIR) are always safe to import — they
carry no API-key dependency.  The ``settings`` singleton at the bottom
validates every required variable on import; any module that needs API keys
should import ``settings`` directly.

db.py intentionally does *not* import from this module so that ``init_db()``
can run without TIINGO_API_KEY / ANTHROPIC_API_KEY being present.
"""

import os
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Always-available constants (no API-key validation)
# ---------------------------------------------------------------------------

DB_PATH: Path = Path(os.environ.get("DB_PATH", "/app/data/wealthagent.db"))
LOG_DIR: Path = Path(os.environ.get("LOG_DIR", "/app/logs"))

_DEFAULT_RSS_FEEDS: list[str] = [
    # Yahoo Finance S&P 500
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    # CNBC – top news and markets
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    # Reuters business & finance
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    # Finviz news
    "https://finviz.com/news_export.ashx?v=1",
    # Seeking Alpha market currents
    "https://seekingalpha.com/market_currents.xml",
    # Investing.com stock news
    "https://www.investing.com/rss/news_25.rss",
]

# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Full runtime configuration.  Required fields raise on instantiation if absent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required ----------------------------------------------------------------
    tiingo_api_key: str
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str

    # --- LLM ---------------------------------------------------------------------
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_timeout: int = 300  # seconds; Pi 5 can take 60–150 s per article
    opus_model: str = "claude-opus-4-6"

    # --- Storage -----------------------------------------------------------------
    db_path: Path = DB_PATH
    log_dir: Path = LOG_DIR

    # --- Portfolio ---------------------------------------------------------------
    monthly_budget_eur: float = 2000.0
    long_term_pct: float = 0.75
    short_term_pct: float = 0.25

    # --- Tax (Irish CGT defaults) ------------------------------------------------
    cgt_rate: float = 0.33
    annual_exemption: float = 1270.0

    # --- Alert thresholds --------------------------------------------------------
    alert_drop_pct: float = 10.0  # flag if price drops >N% in 30 days
    stop_loss_pct: float = 8.0  # exit short-term position if drops >N%
    dividend_yield_max: float = 2.0  # de-prioritise stocks above this yield

    # --- News feeds --------------------------------------------------------------
    rss_feeds: list[str] = Field(default_factory=_DEFAULT_RSS_FEEDS.copy)


# ---------------------------------------------------------------------------
# Module-level validation — raises EnvironmentError with a clear message
# ---------------------------------------------------------------------------

try:
    settings = Settings()
except ValidationError as _exc:
    _missing = [e["loc"][0].upper() for e in _exc.errors() if e.get("type") == "missing"]
    _invalid = [
        f"{e['loc'][0].upper()} ({e['msg']})" for e in _exc.errors() if e.get("type") != "missing"
    ]

    _lines: list[str] = ["WealthAgent: environment configuration error."]
    if _missing:
        _lines += ["", "Missing required variables:"] + [f"  • {v}" for v in _missing]
    if _invalid:
        _lines += ["", "Invalid values:"] + [f"  • {v}" for v in _invalid]
    _lines += ["", "Copy .env.example → .env and fill in the missing values."]

    raise OSError("\n".join(_lines)) from _exc
