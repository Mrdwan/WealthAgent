"""WealthAgent entrypoint.

Initialises the database then execs the Telegram bot process.
Run this file instead of telegram_bot.py directly so that the
schema is always up-to-date on container start.
"""

import logging
import os
import sys
from pathlib import Path


def main() -> None:
    """Bootstrap the application."""
    from config.settings import settings  # noqa: PLC0415
    from log_setup import setup_logging  # noqa: PLC0415

    # 1. Configure logging before anything else
    setup_logging(settings.log_dir)
    log = logging.getLogger(__name__)

    # 2. Ensure the data directory exists before DB init
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    # 3. Initialise / migrate the schema (safe to run every start)
    from db import init_db  # noqa: PLC0415

    init_db()
    log.info("Database ready: %s", settings.db_path)

    # 4. Replace this process with the Telegram bot
    bot = Path(__file__).parent / "telegram_bot.py"
    log.info("Launching bot: %s", bot)
    os.execv(sys.executable, [sys.executable, str(bot)])


if __name__ == "__main__":
    main()
