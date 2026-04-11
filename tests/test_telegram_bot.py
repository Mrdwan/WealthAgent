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
    assert "/buy" in text
    assert "/sell" in text
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


def test_cmd_rebalance_sends_summary_link(monkeypatch):
    """Rebalance sends acknowledgment, then summary + link when save_report succeeds."""
    from datetime import datetime, timedelta

    import telegram_bot
    from db import Report

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    monkeypatch.setattr(telegram_bot.settings, "dashboard_base_url", "http://pi:8080")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    mock_report = Report(
        id=7,
        report_type="rebalance",
        summary="Key rec: Hold AAPL.",
        full_content="Full analysis content...",
        expires_at=datetime.now() + timedelta(days=90),
    )

    mock_reports = mock.MagicMock(save_report=lambda *a, **kw: 7, get_report=lambda _: mock_report)
    with mock.patch.dict(
        "sys.modules",
        {
            "advisor": mock.MagicMock(monthly_rebalance=lambda: "Full analysis content..."),
            "reports": mock_reports,
        },
    ):
        asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))

    assert update.message.reply_text.call_count == 2
    ack = update.message.reply_text.call_args_list[0][0][0]
    result = update.message.reply_text.call_args_list[1][0][0]
    assert "30+" in ack
    assert "Key rec: Hold AAPL." in result
    assert "http://pi:8080/reports/7" in result


def test_cmd_rebalance_uses_default_url_when_base_url_not_set(monkeypatch):
    """Uses localhost:<dashboard_port> when dashboard_base_url is None."""
    from datetime import datetime, timedelta

    import telegram_bot
    from db import Report

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    monkeypatch.setattr(telegram_bot.settings, "dashboard_base_url", None)
    monkeypatch.setattr(telegram_bot.settings, "dashboard_port", 8080)
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    mock_report = Report(
        id=3,
        report_type="rebalance",
        summary="Summary text.",
        full_content="Full content.",
        expires_at=datetime.now() + timedelta(days=90),
    )

    mock_reports2 = mock.MagicMock(save_report=lambda *a, **kw: 3, get_report=lambda _: mock_report)
    with mock.patch.dict(
        "sys.modules",
        {
            "advisor": mock.MagicMock(monthly_rebalance=lambda: "Full content."),
            "reports": mock_reports2,
        },
    ):
        asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))

    result = update.message.reply_text.call_args_list[1][0][0]
    assert "http://localhost:8080/reports/3" in result


def test_cmd_rebalance_fallback_when_save_report_raises(monkeypatch):
    """Falls back to first 500 chars when save_report raises an exception."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()

    full_content = "Hold AAPL " * 100  # 1000 chars

    def _fail_save(*a, **kw):
        raise RuntimeError("DB unavailable")

    with mock.patch.dict(
        "sys.modules",
        {
            "advisor": mock.MagicMock(monthly_rebalance=lambda: full_content),
            "reports": mock.MagicMock(save_report=_fail_save),
        },
    ):
        asyncio.run(telegram_bot.cmd_rebalance(update, mock.MagicMock()))

    assert update.message.reply_text.call_count == 2
    result = update.message.reply_text.call_args_list[1][0][0]
    assert result == full_content[:500]


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
    """Rebalance sends error message when advisor monthly_rebalance fails."""
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


def test_cmd_analyze_sends_summary_link(monkeypatch):
    """Analyze sends acknowledgment, then summary + link when save_report succeeds."""
    from datetime import datetime, timedelta

    import telegram_bot
    from db import Report

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    monkeypatch.setattr(telegram_bot.settings, "dashboard_base_url", "http://pi:8080")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["aapl"]

    mock_report = Report(
        id=5,
        report_type="analyze",
        ticker="AAPL",
        summary="Buy AAPL — strong fundamentals.",
        full_content="Full analysis for AAPL...",
        expires_at=datetime.now() + timedelta(days=90),
    )

    def _analyze(ticker):
        return f"Full analysis for {ticker}..."

    with mock.patch.dict(
        "sys.modules",
        {
            "advisor": mock.MagicMock(analyze_opportunity=_analyze),
            "reports": mock.MagicMock(
                save_report=lambda *a, **kw: 5, get_report=lambda _: mock_report
            ),
        },
    ):
        asyncio.run(telegram_bot.cmd_analyze(update, context))

    assert update.message.reply_text.call_count == 2
    ack = update.message.reply_text.call_args_list[0][0][0]
    result = update.message.reply_text.call_args_list[1][0][0]
    assert "AAPL" in ack  # ticker uppercased
    assert "Buy AAPL" in result
    assert "http://pi:8080/reports/5" in result


def test_cmd_analyze_fallback_when_save_report_raises(monkeypatch):
    """Falls back to first 500 chars when save_report raises an exception."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["TSLA"]

    full_content = "Strong buy TSLA " * 60  # 960 chars

    def _fail_save(*a, **kw):
        raise RuntimeError("DB error")

    with mock.patch.dict(
        "sys.modules",
        {
            "advisor": mock.MagicMock(analyze_opportunity=lambda ticker: full_content),
            "reports": mock.MagicMock(save_report=_fail_save),
        },
    ):
        asyncio.run(telegram_bot.cmd_analyze(update, context))

    assert update.message.reply_text.call_count == 2
    result = update.message.reply_text.call_args_list[1][0][0]
    assert result == full_content[:500]


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
# _run_purge_reports
# ---------------------------------------------------------------------------


def test_run_purge_pipeline_data_calls_purge_all(monkeypatch):
    """_run_purge_pipeline_data calls purge_all and logs the result."""
    import telegram_bot

    mock_purge_all = mock.MagicMock(
        return_value={"news": 2, "alerts": 1, "screener": 0, "fundamentals": 3}
    )
    with mock.patch.dict(
        "sys.modules",
        {"purge": mock.MagicMock(purge_all=mock_purge_all)},
    ):
        telegram_bot._run_purge_pipeline_data()

    mock_purge_all.assert_called_once()


def test_run_purge_reports_calls_purge_and_logs(monkeypatch):
    """_run_purge_reports calls purge_expired_reports and logs the count."""
    import telegram_bot

    mock_purge = mock.MagicMock(return_value=3)
    with mock.patch.dict(
        "sys.modules",
        {"reports": mock.MagicMock(purge_expired_reports=mock_purge)},
    ):
        telegram_bot._run_purge_reports()

    mock_purge.assert_called_once()


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


def test_setup_schedule_registers_six_jobs():
    """
    Registers exactly 6 scheduled jobs
    (hourly, daily, weekly, monthly-check, purge-reports, purge-data).
    """
    import schedule

    import telegram_bot

    schedule.clear()
    try:
        telegram_bot._setup_schedule()
        assert len(schedule.jobs) == 6
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
    """Returns a configured Application with 6 command handlers."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", "tok123")

    mock_app = mock.MagicMock()
    with mock.patch("telegram_bot.Application") as mock_app_cls:
        mock_app_cls.builder.return_value.token.return_value.build.return_value = mock_app
        result = telegram_bot._build_application()

    assert result is mock_app
    mock_app_cls.builder.return_value.token.assert_called_once_with("tok123")
    assert mock_app.add_handler.call_count == 6


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_no_token_runs_scheduler_only(monkeypatch):
    """Without a bot token, starts the scheduler thread and enters sleep loop."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", None)
    monkeypatch.setattr(telegram_bot, "_setup_schedule", mock.MagicMock())
    monkeypatch.setattr(telegram_bot, "setup_logging", mock.MagicMock())

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
    monkeypatch.setattr(telegram_bot, "setup_logging", mock.MagicMock())

    mock_app = mock.MagicMock()
    monkeypatch.setattr(telegram_bot, "_build_application", mock.MagicMock(return_value=mock_app))

    mock_thread = mock.MagicMock()
    with mock.patch.object(telegram_bot.threading, "Thread", return_value=mock_thread):
        telegram_bot.main()

    mock_thread.start.assert_called_once()
    mock_app.run_polling.assert_called_once()


# ---------------------------------------------------------------------------
# _send_long
# ---------------------------------------------------------------------------


def test_send_long_short_message():
    """A message under 4096 chars is sent as a single reply."""
    import telegram_bot

    msg = mock.MagicMock()
    msg.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot._send_long(msg, "Short message"))

    msg.reply_text.assert_called_once_with("Short message")


def test_send_long_chunked_message():
    """A message over 4096 chars is split into multiple replies."""
    import telegram_bot

    msg = mock.MagicMock()
    msg.reply_text = mock.AsyncMock()

    long_text = "A" * (telegram_bot._TELEGRAM_MAX * 2 + 100)
    asyncio.run(telegram_bot._send_long(msg, long_text))

    assert msg.reply_text.call_count == 3
    chunks = [c[0][0] for c in msg.reply_text.call_args_list]
    assert len(chunks[0]) == telegram_bot._TELEGRAM_MAX
    assert len(chunks[1]) == telegram_bot._TELEGRAM_MAX
    assert len(chunks[2]) == 100


# ---------------------------------------------------------------------------
# cmd_buy
# ---------------------------------------------------------------------------


def test_cmd_buy_no_args(monkeypatch):
    """Without arguments, shows usage instructions."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = []

    asyncio.run(telegram_bot.cmd_buy(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]


def test_cmd_buy_missing_args(monkeypatch):
    """With insufficient arguments, shows usage instructions."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "10"]  # missing price and pool

    asyncio.run(telegram_bot.cmd_buy(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]


def test_cmd_buy_invalid_shares(monkeypatch):
    """With non-numeric shares, shows error."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "abc", "150.50", "long_term"]

    asyncio.run(telegram_bot.cmd_buy(update, context))

    update.message.reply_text.assert_called_once()
    assert "Invalid" in update.message.reply_text.call_args[0][0]


def test_cmd_buy_invalid_pool(monkeypatch):
    """With invalid pool value, shows error."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "10", "150.50", "invalid_pool"]

    asyncio.run(telegram_bot.cmd_buy(update, context))

    update.message.reply_text.assert_called_once()
    assert "pool" in update.message.reply_text.call_args[0][0].lower()


def test_cmd_buy_success(monkeypatch):
    """Valid buy command records trade and holding."""
    import telegram_bot
    from db import db_conn

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "10", "150.50", "long_term"]

    asyncio.run(telegram_bot.cmd_buy(update, context))

    update.message.reply_text.assert_called_once()
    msg = update.message.reply_text.call_args[0][0]
    assert "Bought" in msg
    assert "AAPL" in msg
    assert "10" in msg

    # Verify trade was recorded
    with db_conn() as conn:
        trade = conn.execute("SELECT * FROM trades WHERE ticker='AAPL' AND action='buy'").fetchone()
        assert trade is not None
        assert trade["shares"] == 10.0
        assert trade["price_eur"] == 150.50

        holding = conn.execute("SELECT * FROM holdings WHERE ticker='AAPL'").fetchone()
        assert holding is not None
        assert holding["shares"] == 10.0
        assert holding["pool"] == "long_term"


def test_cmd_buy_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_buy(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_sell
# ---------------------------------------------------------------------------


def test_cmd_sell_no_args(monkeypatch):
    """Without arguments, shows usage instructions."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = []

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]


def test_cmd_sell_missing_args(monkeypatch):
    """With insufficient arguments, shows usage instructions."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "5"]  # missing price

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]


def test_cmd_sell_invalid_shares(monkeypatch):
    """With non-numeric shares, shows error."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["AAPL", "abc", "160.00"]

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    assert "Invalid" in update.message.reply_text.call_args[0][0]


def test_cmd_sell_no_holding(monkeypatch):
    """Selling a ticker not in holdings shows error."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["NONEXISTENT", "5", "160.00"]

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    assert "No holding" in update.message.reply_text.call_args[0][0]


def test_cmd_sell_insufficient_shares(monkeypatch):
    """Selling more shares than owned shows error."""
    import telegram_bot
    from db import db_conn

    # Insert a holding first
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,
               purchase_date, pool) VALUES ('TSLA', 5, 100.0, 1.0, '2024-01-01', 'long_term')"""
        )

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["TSLA", "10", "160.00"]  # trying to sell more than owned

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    assert "Insufficient" in update.message.reply_text.call_args[0][0]


def test_cmd_sell_success_partial(monkeypatch):
    """Partial sell reduces holding shares."""
    import telegram_bot
    from db import db_conn

    # Insert a holding first
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,
               purchase_date, pool) VALUES ('GOOGL', 20, 100.0, 1.0, '2024-01-01', 'long_term')"""
        )

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["GOOGL", "5", "120.00"]

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    msg = update.message.reply_text.call_args[0][0]
    assert "Sold" in msg
    assert "GOOGL" in msg

    # Verify trade was recorded and holding updated
    with db_conn() as conn:
        trade = conn.execute(
            "SELECT * FROM trades WHERE ticker='GOOGL' AND action='sell'"
        ).fetchone()
        assert trade is not None
        assert trade["shares"] == 5.0

        holding = conn.execute("SELECT * FROM holdings WHERE ticker='GOOGL'").fetchone()
        assert holding is not None
        assert holding["shares"] == 15.0  # 20 - 5


def test_cmd_sell_success_full(monkeypatch):
    """Selling all shares removes the holding."""
    import telegram_bot
    from db import db_conn

    # Insert a holding first
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO holdings (ticker, shares, entry_price_eur, entry_fx_rate,
               purchase_date, pool) VALUES ('MSFT', 10, 100.0, 1.0, '2024-01-01', 'long_term')"""
        )

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 42
    update.message.reply_text = mock.AsyncMock()
    context = mock.MagicMock()
    context.args = ["MSFT", "10", "120.00"]

    asyncio.run(telegram_bot.cmd_sell(update, context))

    update.message.reply_text.assert_called_once()
    msg = update.message.reply_text.call_args[0][0]
    assert "Sold" in msg

    # Verify holding was removed
    with db_conn() as conn:
        holding = conn.execute("SELECT * FROM holdings WHERE ticker='MSFT'").fetchone()
        assert holding is None


def test_cmd_sell_unauthorized(monkeypatch):
    """Unauthorized user gets no response."""
    import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "telegram_chat_id", "42")
    update = mock.MagicMock()
    update.effective_chat.id = 999
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(telegram_bot.cmd_sell(update, mock.MagicMock()))
    update.message.reply_text.assert_not_called()
