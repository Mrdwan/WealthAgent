"""Unit tests for notifier.py — covers gap lines and branches."""

import sys
from datetime import UTC, datetime
from unittest import mock

import pytest

from alert_engine import Alert
from config.settings import settings

# --- _get_credentials ---


def test_get_credentials_set(monkeypatch):
    from notifier import _get_credentials

    monkeypatch.setattr(settings, "telegram_bot_token", "tok123")
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    token, chat_id = _get_credentials()
    assert token == "tok123"
    assert chat_id == "456"


def test_get_credentials_not_set(monkeypatch):
    from notifier import _get_credentials

    monkeypatch.setattr(settings, "telegram_bot_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)
    token, chat_id = _get_credentials()
    assert token is None
    assert chat_id is None


# --- _split_message ---


def test_split_message_short():
    from notifier import _split_message

    assert _split_message("hello") == ["hello"]


def test_split_message_hard_split():
    """No newlines → hard split at 4096."""
    from notifier import _split_message

    msg = "A" * 5000
    chunks = _split_message(msg)
    assert len(chunks) == 2
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == msg


def test_split_message_newline_split():
    """Long message with newlines splits on newline boundary."""
    from notifier import _split_message

    msg = "\n".join([f"Line {i:04d} padding text here" for i in range(200)])
    assert len(msg) > 4096
    chunks = _split_message(msg)
    assert all(len(c) <= 4096 for c in chunks)


def test_split_message_trailing_newlines():
    """Text that ends exactly at boundary + newlines → while loop exits via falsy text."""
    from notifier import _split_message

    msg = "A" * 4096 + "\n"
    chunks = _split_message(msg)
    assert len(chunks) == 1 or all(len(c) <= 4096 for c in chunks)


# --- send_message ---


def test_send_message_no_token(monkeypatch, capsys):
    from notifier import send_message

    monkeypatch.setattr(settings, "telegram_bot_token", None)
    send_message("Hello test")
    assert "Hello test" in capsys.readouterr().out


def test_send_message_with_token(monkeypatch):
    from notifier import send_message

    monkeypatch.setattr(settings, "telegram_bot_token", "tok123")
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    resp = mock.MagicMock()
    resp.raise_for_status = mock.MagicMock()
    with mock.patch("notifier.requests.post", return_value=resp) as mock_post:
        send_message("Test message")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "tok123" in call_kwargs[0][0]  # URL contains token


def test_send_message_failure(monkeypatch):
    import requests

    from notifier import send_message

    monkeypatch.setattr(settings, "telegram_bot_token", "tok123")
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    with (
        mock.patch(
            "notifier.requests.post",
            side_effect=requests.exceptions.RequestException("send err"),
        ),
        pytest.raises(requests.exceptions.RequestException),
    ):
        send_message("Test")


# --- _format_alert ---


def test_format_alert_price_drop():
    from notifier import _format_alert

    alert = Alert(
        type="price_drop",
        ticker="TSLA",
        details={
            "drop_pct": -21.5,
            "current_price_eur": 160.0,
            "prior_price_eur": 200.0,
            "current_date": "2024-04-09",
            "prior_date": "2024-03-10",
            "threshold_pct": 10.0,
        },
        triggered_at=datetime.now(tz=UTC),
    )
    text = _format_alert(alert)
    assert "TSLA" in text
    assert "-21.5" in text


def test_format_alert_news_signal():
    from notifier import _format_alert

    alert = Alert(
        type="news_signal",
        ticker="NVDA",
        details={
            "sentiment": "negative",
            "catalyst": "regulation",
            "timeframe": "months",
            "summary": "Export controls",
            "confidence": 0.8,
            "signal_id": 1,
            "article_id": 1,
        },
        triggered_at=datetime.now(tz=UTC),
    )
    text = _format_alert(alert)
    assert "NVDA" in text
    assert "negative" in text


def test_format_alert_opportunity():
    from notifier import _format_alert

    alert = Alert(
        type="opportunity",
        ticker="AMZN",
        details={
            "sentiment": "positive",
            "catalyst": "earnings",
            "timeframe": "weeks",
            "summary": "Beat expectations",
            "confidence": 0.9,
            "signal_id": 1,
            "article_id": 1,
        },
        triggered_at=datetime.now(tz=UTC),
    )
    text = _format_alert(alert)
    assert "AMZN" in text


def test_format_alert_unknown_type():
    """Generic fallback for unknown alert types."""
    from notifier import _format_alert

    alert = Alert(
        type="custom_type",
        ticker="XYZ",
        details={"key1": "val1", "key2": "val2"},
        triggered_at=datetime.now(tz=UTC),
    )
    text = _format_alert(alert)
    assert "key1: val1" in text
    assert "key2: val2" in text


# --- send_alert ---


def test_send_alert(monkeypatch, capsys):
    from notifier import send_alert

    monkeypatch.setattr(settings, "telegram_bot_token", None)
    alert = Alert(
        type="price_drop",
        ticker="TSLA",
        details={"drop_pct": -10.0},
        triggered_at=datetime.now(tz=UTC),
    )
    send_alert(alert)  # stdout mode
    assert "TSLA" in capsys.readouterr().out


# --- main ---


def test_main_no_args(monkeypatch):
    import notifier

    monkeypatch.setattr(sys, "argv", ["notifier"])
    with pytest.raises(SystemExit) as exc_info:
        notifier.main()
    assert exc_info.value.code == 1


def test_main_with_args(monkeypatch, capsys):
    import notifier

    monkeypatch.setattr(sys, "argv", ["notifier", "Hello", "World"])
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    notifier.main()
    out = capsys.readouterr().out
    assert "Hello World" in out
