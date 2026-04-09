"""Telegram bot — placeholder.

This module will be replaced with the full bot implementation.
For now it just confirms the process started successfully.
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    """Start the Telegram bot."""
    log.info("WealthAgent bot starting — replace this placeholder with real logic.")
    # TODO: implement python-telegram-bot Application here
    # Keep the process alive so Docker doesn't restart-loop
    import signal
    import time

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
