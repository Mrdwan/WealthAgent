"""Unit tests for config/settings.py — module-level validation error paths."""

import importlib
import os

import pytest


def test_settings_module_level_missing_vars(tmp_path, monkeypatch):
    """Reload with missing required vars triggers OSError with 'Missing required variables'.

    Uses tmp_path as cwd so pydantic-settings doesn't read .env from the project root.
    """
    import config.settings as settings_mod

    monkeypatch.chdir(tmp_path)

    saved = {}
    for key in ("TIINGO_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        saved[key] = os.environ.pop(key, None)

    try:
        with pytest.raises(OSError, match="Missing required variables"):
            importlib.reload(settings_mod)
    finally:
        for key, val in saved.items():
            if val is not None:
                os.environ[key] = val
        importlib.reload(settings_mod)


def test_settings_module_level_invalid_values(tmp_path, monkeypatch):
    """Reload with an invalid float value triggers OSError with 'Invalid values'."""
    import config.settings as settings_mod

    monkeypatch.chdir(tmp_path)
    os.environ["MONTHLY_BUDGET_EUR"] = "not-a-number"

    try:
        with pytest.raises(OSError, match="Invalid values"):
            importlib.reload(settings_mod)
    finally:
        os.environ.pop("MONTHLY_BUDGET_EUR", None)
        importlib.reload(settings_mod)
