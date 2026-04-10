"""Unit tests for telegram_bot.py."""

import asyncio
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# _is_authorized
# ---------------------------------------------------------------------------


def test_is_authorized_match(monkeypatch):
    """Returns True when chat_id (int) matches configured TELEGRAM_CHAT_ID."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    assert telegram_bot._is_authorized(42) is True


def test_is_authorized_match_string(monkeypatch):
    """Returns True when chat_id (str) matches configured TELEGRAM_CHAT_ID."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    assert telegram_bot._is_authorized("42") is True


def test_is_authorized_no_match(monkeypatch):
    """Returns False when chat_id does not match TELEGRAM_CHAT_ID."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    assert telegram_bot._is_authorized(999) is False


def test_is_authorized_strips_whitespace(monkeypatch):
    """Returns True when configured value has surrounding whitespace."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "  42  ")
    assert telegram_bot._is_authorized(42) is True


def test_is_authorized_no_config(monkeypatch):
    """Returns False when TELEGRAM_CHAT_ID is not configured."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", None)
    assert telegram_bot._is_authorized(42) is False


# ---------------------------------------------------------------------------
# _authorized_only decorator
# ---------------------------------------------------------------------------


def test_authorized_only_calls_handler(monkeypatch):
    """Decorated handler is called for authorized chat."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")

    called = []

    @telegram_bot._authorized_only
    async def handler(update, context):
        called.append(True)

    update = mock.MagicMock()
    update.effective_chat.id = 42
    asyncio.run(handler(update, mock.MagicMock()))
    assert called == [True]


def test_authorized_only_ignores_unauthorized(monkeypatch):
    """Decorated handler is not called for unauthorized chat."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")

    called = []

    @telegram_bot._authorized_only
    async def handler(update, context):
        called.append(True)

    update = mock.MagicMock()
    update.effective_chat.id = 999
    asyncio.run(handler(update, mock.MagicMock()))
    assert called == []


def test_authorized_only_preserves_name():
    """Decorator preserves the wrapped function's __name__."""
    import telegram_bot

    @telegram_bot._authorized_only
    async def my_handler(update, context):
        pass  # pragma: no cover

    assert my_handler.__name__ == "my_handler"


# ---------------------------------------------------------------------------
# cmd_help
# ---------------------------------------------------------------------------


def test_cmd_help_authorized(monkeypatch):
    """Authorized user receives the help text."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_help(update, mock.MagicMock()))

    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "/status" in text
    assert "/rebalance" in text
    assert "/analyze" in text
    assert "/help" in text


def test_cmd_help_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_help(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def test_cmd_status_authorized(monkeypatch):
    """Authorized user receives portfolio summary."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    with mock.patch.dict(
        "sys.modules",
        {"context_builder": mock.MagicMock(build_holdings_summary=lambda: "Portfolio: €10,000")},
    ):
        asyncio.run(telegram_bot.cmd_status(update, mock.MagicMock()))

    update.message.reply_text.assert_called_once_with("Portfolio: €10,000")


def test_cmd_status_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_status(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_rebalance
# ---------------------------------------------------------------------------


def test_cmd_rebalance_sends_ack_then_result(monkeypatch):
    """Rebalance sends acknowledgment first, then the advisor result."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    with mock.patch.dict(
        "sys.modules",
        {"advisor": mock.MagicMock(monthly_rebalance=lambda: "Hold AAPL")},
    ):
        asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))

    assert update.message.reply_text.call_count == 2
    ack = update.message.reply_text.call_args_list[0][0][0]
    result = update.message.reply_text.call_args_list[1][0][0]
    assert "30+" in ack
    assert "Hold AAPL" in result


def test_cmd_rebalance_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()


def test_cmd_rebalance_handles_error(monkeypatch):
    """Rebalance sends error message when advisor fails."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    def _fail():
        raise RuntimeError("LLM unavailable")

    with mock.patch.dict("sys.modules", {"advisor": mock.MagicMock(monthly_rebalance=_fail)}):
        asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))

    assert update.message.reply_text.call_count == 2
    error_msg = update.message.reply_text.call_args_list[1][0][0]
    assert "failed" in error_msg.lower()


# ---------------------------------------------------------------------------
# cmd_analyze
# ---------------------------------------------------------------------------


def test_cmd_analyze_no_args(monkeypatch):
    """Without a ticker argument, shows usage instructions."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = None

    asyncio.run(telegram_bot.cmd_analyze(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]


def test_cmd_analyze_sends_ack_then_result(monkeypatch):
    """Analyze sends acknowledgment first, then the advisor result."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["aapl"]

    with mock.patch.dict(
        "sys.modules",
        {"advisor": mock.MagicMock(analyze_opportunity=lambda ticker: f"Buy {ticker}")},
    ):
        asyncio.run(telegram_bot.cmd_analyze(update, context))

    assert update.message.reply_text.call_count == 2
    ack = update.message.reply_text.call_args_list[0][0][0]
    result = update.message.reply_text.call_args_list[1][0][0]
    assert "AAPL" in ack  # ticker uppercased
    assert "Buy AAPL" in result


def test_cmd_analyze_handles_error(monkeypatch):
    """Analyze sends error message when advisor fails."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["TSLA"]

    def _fail(ticker):
        raise RuntimeError("timeout")

    with mock.patch.dict("sys.modules", {"advisor": mock.MagicMock(analyze_opportunity=_fail)}):
        asyncio.run(telegram_bot.cmd_analyze(update, context))

    assert update.message.reply_text.call_count == 2
    error_msg = update.message.reply_text.call_args_list[1][0][0]
    assert "failed" in error_msg.lower()


def test_cmd_analyze_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_analyze(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# _safe_run
# ---------------------------------------------------------------------------


def test_safe_run_success():
    """Calls the pipeline function and completes without error."""
    import telegram_bot

    calls = []
    telegram_bot._safe_run(lambda: calls.append(1), "test")
    assert calls == [1]


def test_safe_run_catches_exception():
    """Logs and swallows exceptions — does not re-raise."""
    import telegram_bot

    telegram_bot._safe_run(lambda: (_ for _ in ()).throw(RuntimeError("boom")), "failing")
    # No exception raised — test passes by reaching this line


# ---------------------------------------------------------------------------
# _monthly_check
# ---------------------------------------------------------------------------


def test_monthly_check_runs_on_first(monkeypatch):
    """Calls _safe_run with cmd_monthly on the 1st of the month."""
    import telegram_bot

    mock_dt = mock.MagicMock()
    mock_dt.now.return_value.day = 1
    monkeypatch.setattr(telegram_bot, "datetime", mock_dt)

    mock_safe = mock.MagicMock()
    monkeypatch.setattr(telegram_bot, "_safe_run", mock_safe)

    telegram_bot._monthly_check()

    mock_safe.assert_called_once()
    assert mock_safe.call_args[0][1] == "monthly"


def test_monthly_check_skips_other_days(monkeypatch):
    """Does nothing on days other than the 1st."""
    import telegram_bot

    mock_dt = mock.MagicMock()
    mock_dt.now.return_value.day = 15
    monkeypatch.setattr(telegram_bot, "datetime", mock_dt)

    mock_safe = mock.MagicMock()
    monkeypatch.setattr(telegram_bot, "_safe_run", mock_safe)

    telegram_bot._monthly_check()

    mock_safe.assert_not_called()


# ---------------------------------------------------------------------------
# _setup_schedule
# ---------------------------------------------------------------------------


def test_setup_schedule_registers_four_jobs():
    """Registers exactly 4 scheduled jobs."""
    import schedule

    import telegram_bot

    schedule.clear()
    try:
        telegram_bot._setup_schedule()
        assert len(schedule.jobs) == 4
    finally:
        schedule.clear()


# ---------------------------------------------------------------------------
# _run_scheduler_loop
# ---------------------------------------------------------------------------


def test_run_scheduler_loop_calls_run_pending(monkeypatch):
    """Calls schedule.run_pending() and time.sleep(60) on each iteration."""
    import telegram_bot

    iterations = [0]

    def fake_sleep(secs):
        assert secs == 60
        iterations[0] += 1
        if iterations[0] >= 2:
            raise SystemExit(0)

    with (
        mock.patch.object(telegram_bot.schedule, "run_pending") as mock_pending,
        mock.patch.object(telegram_bot.time, "sleep", side_effect=fake_sleep),
        pytest.raises(SystemExit),
    ):
        telegram_bot._run_scheduler_loop()

    assert mock_pending.call_count == 2


# ---------------------------------------------------------------------------
# _build_application
# ---------------------------------------------------------------------------


def test_build_application_no_token(monkeypatch):
    """Returns None when TELEGRAM_BOT_TOKEN is not configured."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", None)
    assert telegram_bot._build_application() is None


def test_build_application_with_token(monkeypatch):
    """Returns a configured Application with 4 command handlers."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", "tok123")

    mock_app = mock.MagicMock()
    with mock.patch("telegram_bot.Application") as mock_app_cls:
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        result = telegram_bot._build_application()

    assert result is mock_app
    mock_app_cls.builder.return_value.token.assert_called_once_with("tok123")
    assert mock_app.add_handler.call_count == 4


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_no_token_runs_scheduler_only(monkeypatch):
    """Without a bot token, starts the scheduler thread and enters sleep loop."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", None)
    monkeypatch.setattr(telegram_bot, "_setup_schedule", mock.MagicMock())

    mock_thread = mock.MagicMock()
    with (
        mock.patch.object(telegram_bot.threading, "Thread", return_value=mock_thread),
        mock.patch.object(telegram_bot.time, "sleep", side_effect=SystemExit(0)),
        pytest.raises(SystemExit),
    ):
        telegram_bot.main()

    telegram_bot._setup_schedule.assert_called_once()
    mock_thread.start.assert_called_once()


def test_main_with_token_starts_polling(monkeypatch):
    """With a bot token, starts the scheduler thread and calls app.run_polling()."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", "tok123")
    monkeypatch.setattr(telegram_bot, "_setup_schedule", mock.MagicMock())

    mock_app = mock.MagicMock()
    monkeypatch.setattr(telegram_bot, "_build_application", mock.MagicMock(return_value=mock_app))

    mock_thread = mock.MagicMock()
    with mock.patch.object(telegram_bot.threading, "Thread", return_value=mock_thread):
        telegram_bot.main()

    mock_thread.start.assert_called_once()
    mock_app.run_polling.assert_called_once()
