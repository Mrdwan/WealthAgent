"""Pipeline runner — single CLI entry point for all scheduled tasks.

Each command orchestrates a sequence of pipeline steps.  Top-level exception
handling ensures one failure does not kill the entire run.

CLI usage (inside the container):
    python -m run_pipeline hourly
    python -m run_pipeline daily
    python -m run_pipeline rebalance
    python -m run_pipeline status
"""

import logging
import sys
from datetime import datetime

from config.settings import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    """Configure logging to stdout and a monthly log file."""
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file),
        ],
        force=True,
    )


# ---------------------------------------------------------------------------
# Pipeline commands
# ---------------------------------------------------------------------------


def cmd_prices() -> None:
    """Fetch prices for all holdings."""
    from price_fetcher import fetch_all_prices

    fetch_all_prices()


def cmd_hourly() -> None:
    """Hourly pipeline: news -> extract signals -> alerts -> notify."""
    from alert_engine import run_all_checks
    from news_extractor import process_unprocessed
    from news_fetcher import fetch_all_feeds
    from notifier import send_alert

    fetch_all_feeds()
    process_unprocessed(use_confidence_scoring=False)
    alerts = run_all_checks()
    for alert in alerts:
        try:
            send_alert(alert)
        except Exception as exc:
            log.error("Failed to send alert: %s", exc)


def cmd_daily() -> None:
    """Daily pipeline: FX rates -> prices -> hourly tasks."""
    from fx_fetcher import fetch_ecb_rates

    fetch_ecb_rates()
    cmd_prices()
    cmd_hourly()


def cmd_iwda() -> None:
    """Fetch the latest IWDA top-N holdings."""
    from iwda_fetcher import fetch_iwda_holdings

    fetch_iwda_holdings()


def cmd_rebalance() -> None:
    """Generate and send a rebalance recommendation via the advisor."""
    from advisor import monthly_rebalance
    from notifier import send_message

    recommendation = monthly_rebalance()
    send_message(recommendation)
    print(recommendation)


def cmd_status() -> None:
    """Build and send a portfolio status summary."""
    from context_builder import build_holdings_summary
    from notifier import send_message

    summary = build_holdings_summary()
    send_message(summary)
    print(summary)


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------

_COMMAND_NAMES = ("hourly", "prices", "daily", "iwda", "rebalance", "status")


def main() -> None:
    """Dispatch the requested pipeline command."""
    _setup_logging()

    if len(sys.argv) < 2 or sys.argv[1] not in _COMMAND_NAMES:
        print(f"Usage: python -m run_pipeline {{{' | '.join(_COMMAND_NAMES)}}}")
        sys.exit(1)

    cmd_name = sys.argv[1]
    cmd_func = globals()[f"cmd_{cmd_name}"]
    log.info("Starting pipeline command: %s", cmd_name)

    try:
        cmd_func()
        log.info("Pipeline command '%s' completed successfully.", cmd_name)
    except Exception as exc:
        log.error("Pipeline command '%s' failed: %s", cmd_name, exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
