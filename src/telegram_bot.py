"""Telegram bot — placeholder.

This module will be replaced with the full bot implementation.
For now it just confirms the process started successfully.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    """Start the Telegram bot."""
    log.info("WealthAgent bot starting — replace this placeholder with real logic.")
    # TODO: implement python-telegram-bot Application here


if __name__ == "__main__":
    main()
