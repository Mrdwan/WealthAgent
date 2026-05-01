"""Application settings for WealthAgent.

All values come from environment variables (with sensible defaults).
A ``.env`` file in the project root is loaded automatically if present.

API keys are ``None`` when not set — each consumer validates at point of use.
This means ``from config.settings import settings`` is always safe to import.
"""

import json
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_RSS_FEEDS: list[str] = [
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


def parse_rss_feeds(raw: str) -> list[str]:
    """Parse an RSS_FEEDS string — accepts JSON array or comma-separated URLs."""
    if not raw:
        return DEFAULT_RSS_FEEDS.copy()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(u) for u in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return [u.strip() for u in raw.split(",") if u.strip()]


class _RssAwareEnvSource(EnvSettingsSource):
    """Env source that normalises RSS_FEEDS to a JSON array before decoding."""

    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        """Pre-process rss_feeds so CSV strings survive JSON decoding."""
        if field_name == "rss_feeds" and isinstance(value, str):
            urls = parse_rss_feeds(value)
            encoded = json.dumps(urls)
            return super().prepare_field_value(field_name, field, encoded, value_is_complex)
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    """Runtime configuration.  API keys are None when not set."""

    model_config = SettingsConfigDict(env_ignore_empty=True)

    # Storage
    db_path: Path = Path("/app/data/wealthagent.db")
    log_dir: Path = Path("/app/logs")

    # API keys — validated at point of use, not here
    tiingo_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # LLM — local Ollama for cheap inference
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_timeout: int = 300

    # LLM — advisor (deep analysis via OpenAI-compatible API)
    advisor_model: str = "claude-opus-4-6"
    advisor_api_key: str | None = None
    advisor_base_url: str | None = None

    # Portfolio
    monthly_budget_eur: float = 2000.0
    long_term_pct: float = 0.75
    short_term_pct: float = 0.25
    monthly_stocks_eur: float = 1050.0  # individual-stock allocation per month
    monthly_etf_eur: float = 450.0  # IWDA ETF allocation per month
    monthly_buffer_eur: float = 500.0  # flexible buffer the LLM allocates
    iwda_top_n: int = 15  # number of top IWDA holdings to mirror
    iwda_exit_buffer: int = 5  # hysteresis: only sell if dropped to rank > top_n + this

    # Tax (Irish CGT defaults)
    cgt_rate: float = 0.33
    annual_exemption: float = 1270.0

    # Alert thresholds
    alert_drop_pct: float = 10.0
    stop_loss_pct: float = 8.0
    dividend_yield_max: float = 2.0

    # News feeds
    rss_feeds: list[str] = DEFAULT_RSS_FEEDS.copy()

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_port: int = 8080
    dashboard_secret_key: str | None = None
    dashboard_base_url: str | None = None  # e.g. http://192.168.1.x:8080

    # Report retention
    report_retention_days: int = 90

    # Pipeline data retention (4× the context-builder look-back windows)
    news_retention_days: int = 28  # 4 × 7-day signal window
    alerts_retention_days: int = 28  # 4 × 7-day alert window
    screener_retention_days: int = 120  # 4 × monthly screener cycle
    fundamentals_retention_days: int = 28  # 4 × weekly fetch cycle

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Replace the default env source with one that handles CSV RSS_FEEDS."""
        return (
            init_settings,
            _RssAwareEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()
