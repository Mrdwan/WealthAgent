"""Telegram bot — command handlers and scheduled tasks for WealthAgent.

Handles /status, /rebalance, /analyze, and /help commands via python-telegram-bot.
Runs scheduled pipeline tasks (hourly, daily, weekly, monthly) in a background thread.

If TELEGRAM_BOT_TOKEN is not set, runs in scheduler-only mode with console output.
"""

import asyncio
import functools
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import schedule
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import settings

log = logging.getLogger(__name__)

_HELP_TEXT = """\
WealthAgent commands:

/status — portfolio summary
/rebalance — AI rebalance recommendations (30+ seconds)
/analyze TICKER — deep analysis of a ticker (30+ seconds)
/help — show this message\
"""

# Thread pool for blocking LLM calls — limited to 2 workers to conserve Pi resources
_executor = ThreadPoolExecutor(max_workers=2)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def _is_authorized(chat_id: int | str) -> bool:
    """Return True if chat_id matches the configured TELEGRAM_CHAT_ID."""
    configured = settings.telegram_chat_id
    if not configured:
        return False
    return str(chat_id) == configured.strip()


def _authorized_only(handler: Callable) -> Callable:
    """Decorator: silently ignore command handlers from unauthorized chats."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        return await handler(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


@_authorized_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show available commands."""
    await update.message.reply_text(_HELP_TEXT)


@_authorized_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show portfolio summary."""
    from context_builder import build_holdings_summary  # noqa: PLC0415

    summary = build_holdings_summary()
    await update.message.reply_text(summary)


@_authorized_only
async def cmd_rebalance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /rebalance — run advisor LLM and send rebalance recommendations."""
    from advisor import monthly_rebalance  # noqa: PLC0415

    await update.message.reply_text("Generating rebalance recommendations\u2026 (30+ seconds)")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_executor, monthly_rebalance)
        await update.message.reply_text(result)
    except Exception as exc:
        log.error("Rebalance failed: %s", exc)
        await update.message.reply_text(f"Rebalance failed: {exc}")


@_authorized_only
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /analyze TICKER — deep analysis of a ticker via advisor LLM."""
    from advisor import analyze_opportunity  # noqa: PLC0415

    if not context.args:
        await update.message.reply_text("Usage: /analyze TICKER")
        return

    ticker = context.args[0].upper()
    await update.message.reply_text(f"Analyzing {ticker}\u2026 (30+ seconds)")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_executor, analyze_opportunity, ticker)
        await update.message.reply_text(result)
    except Exception as exc:
        log.error("Analyze failed for %s: %s", ticker, exc)
        await update.message.reply_text(f"Analysis of {ticker} failed: {exc}")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def _safe_run(cmd_fn: Callable[[], None], name: str) -> None:
    """Run a pipeline command, logging errors without crashing the scheduler."""
    log.info("Running scheduled task: %s", name)
    try:
        cmd_fn()
        log.info("Scheduled task '%s' completed", name)
    except Exception as exc:
        log.error("Scheduled task '%s' failed: %s", name, exc, exc_info=True)


def _monthly_check() -> None:
    """Run monthly pipeline on the 1st of each month at 08:00."""
    from run_pipeline import cmd_monthly  # noqa: PLC0415

    if datetime.now().day == 1:
        _safe_run(cmd_monthly, "monthly")


def _setup_schedule() -> None:
    """Register all scheduled pipeline tasks."""
    from run_pipeline import cmd_daily, cmd_hourly, cmd_weekly  # noqa: PLC0415

    schedule.every().hour.do(_safe_run, cmd_hourly, "hourly")
    schedule.every().day.at("06:00").do(_safe_run, cmd_daily, "daily")
    schedule.every().sunday.at("07:00").do(_safe_run, cmd_weekly, "weekly")
    schedule.every().day.at("08:00").do(_monthly_check)


def _run_scheduler_loop() -> None:
    """Blocking scheduler loop — intended to run in a daemon thread."""
    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Bot application
# ---------------------------------------------------------------------------


def _build_application() -> Application | None:
    """Build the Telegram bot Application, or return None if no token configured."""
    token = settings.telegram_bot_token
    if not token:
        return None
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("rebalance", cmd_rebalance))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Telegram bot and background scheduler.

    If TELEGRAM_BOT_TOKEN is not set, runs in scheduler-only mode.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("WealthAgent bot starting\u2026")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    _setup_schedule()
    scheduler_thread = threading.Thread(target=_run_scheduler_loop, daemon=True)
    scheduler_thread.start()
    log.info("Scheduler started (thread: %s)", scheduler_thread.name)

    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set \u2014 running scheduler only (no bot)")
        while True:
            time.sleep(3600)

    app = _build_application()
    log.info("Starting Telegram bot polling\u2026")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
