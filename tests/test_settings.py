"""Unit tests for config/settings.py (pydantic-settings)."""

from pathlib import Path

# --- Settings defaults ---


def test_settings_defaults(monkeypatch):
    """Settings with no env vars gives sensible defaults."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)

    from config.settings import Settings

    s = Settings()
    assert s.db_path == Path("/app/data/wealthagent.db")
    assert s.tiingo_api_key is None
    assert s.ollama_timeout == 300
    assert len(s.rss_feeds) > 0
    assert s.advisor_model == "claude-opus-4-6"
    assert s.advisor_api_key is None
    assert s.advisor_base_url is None


def test_settings_custom_values():
    """Settings can be constructed with explicit keyword values."""
    from config.settings import Settings

    s = Settings(tiingo_api_key="my-key", ollama_timeout=60)
    assert s.tiingo_api_key == "my-key"
    assert s.ollama_timeout == 60


# --- Env var loading (automatic via pydantic-settings) ---


def test_settings_reads_env(monkeypatch):
    """Settings automatically reads env vars."""
    monkeypatch.setenv("TIINGO_API_KEY", "from-env")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "120")

    from config.settings import Settings

    s = Settings()
    assert s.tiingo_api_key == "from-env"
    assert s.ollama_timeout == 120


def test_settings_defaults_when_unset(monkeypatch):
    """Unset env vars fall back to field defaults."""
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)

    from config.settings import Settings

    s = Settings()
    assert s.tiingo_api_key is None
    assert s.ollama_base_url == "http://ollama:11434"


# --- parse_rss_feeds (pure function) ---


def test_parse_rss_feeds_empty():
    """Empty string returns the default feed list."""
    from config.settings import parse_rss_feeds

    feeds = parse_rss_feeds("")
    assert len(feeds) > 0


def test_parse_rss_feeds_json():
    """JSON array is parsed correctly."""
    from config.settings import parse_rss_feeds

    feeds = parse_rss_feeds('["https://a.com", "https://b.com"]')
    assert feeds == ["https://a.com", "https://b.com"]


def test_parse_rss_feeds_json_non_list():
    """JSON that parses but isn't a list falls through to CSV split."""
    from config.settings import parse_rss_feeds

    feeds = parse_rss_feeds('{"not": "a list"}')
    assert len(feeds) > 0


def test_parse_rss_feeds_csv():
    """Comma-separated URLs are split correctly."""
    from config.settings import parse_rss_feeds

    feeds = parse_rss_feeds("https://a.com, https://b.com")
    assert feeds == ["https://a.com", "https://b.com"]


def test_parse_rss_feeds_invalid_json_falls_to_csv():
    """Malformed JSON falls through to CSV split."""
    from config.settings import parse_rss_feeds

    feeds = parse_rss_feeds("{bad json, https://a.com")
    assert "https://a.com" in feeds


# --- RSS_FEEDS env var integration ---


def test_rss_feeds_empty_env_uses_defaults(monkeypatch):
    """Empty RSS_FEEDS env var is ignored; defaults are used."""
    monkeypatch.setenv("RSS_FEEDS", "")

    from config.settings import Settings

    s = Settings()
    assert len(s.rss_feeds) > 0


def test_rss_feeds_unset_env_uses_defaults(monkeypatch):
    """Absent RSS_FEEDS env var uses defaults."""
    monkeypatch.delenv("RSS_FEEDS", raising=False)

    from config.settings import Settings

    s = Settings()
    assert len(s.rss_feeds) > 0


def test_rss_feeds_json_env(monkeypatch):
    """RSS_FEEDS env var with JSON array works end-to-end."""
    monkeypatch.setenv("RSS_FEEDS", '["https://a.com", "https://b.com"]')

    from config.settings import Settings

    s = Settings()
    assert s.rss_feeds == ["https://a.com", "https://b.com"]


def test_rss_feeds_csv_env(monkeypatch):
    """RSS_FEEDS env var with comma-separated URLs works end-to-end."""
    monkeypatch.setenv("RSS_FEEDS", "https://a.com, https://b.com")

    from config.settings import Settings

    s = Settings()
    assert s.rss_feeds == ["https://a.com", "https://b.com"]


def test_rss_feeds_json_non_list_env(monkeypatch):
    """RSS_FEEDS env var with non-list JSON falls to CSV split."""
    monkeypatch.setenv("RSS_FEEDS", '{"not": "a list"}')

    from config.settings import Settings

    s = Settings()
    assert len(s.rss_feeds) > 0


def test_rss_feeds_invalid_json_env(monkeypatch):
    """RSS_FEEDS env var with broken JSON falls to CSV split."""
    monkeypatch.setenv("RSS_FEEDS", "{bad json, https://a.com")

    from config.settings import Settings

    s = Settings()
    assert "https://a.com" in s.rss_feeds


# --- Dashboard settings ---


def test_dashboard_defaults(monkeypatch):
    """Dashboard settings have sensible defaults."""
    monkeypatch.delenv("DASHBOARD_ENABLED", raising=False)
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)
    monkeypatch.delenv("DASHBOARD_SECRET_KEY", raising=False)
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)

    from config.settings import Settings

    s = Settings(_env_file=None)
    assert s.dashboard_enabled is True
    assert s.dashboard_port == 8080
    assert s.dashboard_secret_key is None
    assert s.dashboard_base_url is None


def test_report_retention_default():
    """report_retention_days defaults to 90."""
    from config.settings import Settings

    s = Settings()
    assert s.report_retention_days == 90


def test_dashboard_settings_from_env(monkeypatch):
    """Dashboard settings are read from env vars."""
    monkeypatch.setenv("DASHBOARD_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_PORT", "9090")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "supersecret")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://192.168.1.10:8080")
    monkeypatch.setenv("REPORT_RETENTION_DAYS", "30")

    from config.settings import Settings

    s = Settings()
    assert s.dashboard_enabled is False
    assert s.dashboard_port == 9090
    assert s.dashboard_secret_key == "supersecret"
    assert s.dashboard_base_url == "http://192.168.1.10:8080"
    assert s.report_retention_days == 30


# --- IWDA / monthly allocation settings ---


def test_iwda_settings_defaults():
    """New IWDA and monthly allocation settings have correct defaults."""
    from config.settings import Settings

    s = Settings()
    assert s.monthly_stocks_eur == 1050.0
    assert s.monthly_etf_eur == 450.0
    assert s.monthly_buffer_eur == 500.0
    assert s.iwda_top_n == 15
    assert s.iwda_exit_buffer == 5


def test_iwda_settings_from_env(monkeypatch):
    """IWDA and monthly allocation settings are read from env vars."""
    monkeypatch.setenv("MONTHLY_STOCKS_EUR", "800.0")
    monkeypatch.setenv("MONTHLY_ETF_EUR", "300.0")
    monkeypatch.setenv("MONTHLY_BUFFER_EUR", "200.0")
    monkeypatch.setenv("IWDA_TOP_N", "20")
    monkeypatch.setenv("IWDA_EXIT_BUFFER", "3")

    from config.settings import Settings

    s = Settings()
    assert s.monthly_stocks_eur == 800.0
    assert s.monthly_etf_eur == 300.0
    assert s.monthly_buffer_eur == 200.0
    assert s.iwda_top_n == 20
    assert s.iwda_exit_buffer == 3
