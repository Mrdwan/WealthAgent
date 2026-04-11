"""Logging setup for WealthAgent.

Configures the root logger with:
- A console handler (stdout)
- A daily rotating file handler writing to DD-MM-YYYY.log in the log directory

Purges log files from previous months on startup to keep disk usage bounded.
"""

import logging
import re
from datetime import date
from logging.handlers import BaseRotatingHandler
from pathlib import Path

log = logging.getLogger(__name__)

_DATE_PATTERN = re.compile(r"^(\d{2})-(\d{2})-(\d{4})\.log$")


class DailyLogHandler(BaseRotatingHandler):
    """File handler that writes to DD-MM-YYYY.log and rolls over at midnight."""

    def __init__(self, log_dir: Path) -> None:
        """Initialise, opening today's date-stamped log file."""
        self.log_dir = log_dir
        filename = str(log_dir / date.today().strftime("%d-%m-%Y.log"))
        super().__init__(filename, mode="a", encoding="utf-8", delay=False)

    def _today_path(self) -> str:
        """Return the absolute path for today's log file."""
        return str(self.log_dir / date.today().strftime("%d-%m-%Y.log"))

    def shouldRollover(self, record: logging.LogRecord) -> int:  # noqa: N802
        """Return 1 if the date has changed since the handler was opened."""
        return 1 if self._today_path() != self.baseFilename else 0

    def doRollover(self) -> None:  # noqa: N802
        """Switch to a new file for today's date."""
        if self.stream:
            self.stream.flush()
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        self.baseFilename = self._today_path()
        self.stream = self._open()


def _parse_log_date(name: str) -> date | None:
    """Parse a date from a DD-MM-YYYY.log filename, or return None."""
    m = _DATE_PATTERN.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def purge_old_logs(log_dir: Path) -> None:
    """Delete log files from months before the current month.

    Skips files whose names do not match the DD-MM-YYYY.log pattern.
    Safe to call when log_dir does not yet exist.
    """
    if not log_dir.exists():
        return
    today = date.today()
    for path in log_dir.glob("*.log"):
        d = _parse_log_date(path.name)
        if d is None:
            continue
        if (d.year, d.month) < (today.year, today.month):
            path.unlink()
            log.info("Purged old log: %s", path.name)


def setup_logging(log_dir: Path) -> None:
    """Configure the root logger with console and daily rotating file handlers.

    Clears any existing handlers first so this is safe to call multiple times
    (e.g. from entrypoint and then again from telegram_bot after execv).
    Also purges log files from previous months.

    Args:
        log_dir: Directory where daily log files are written.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = DailyLogHandler(log_dir)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    purge_old_logs(log_dir)
    log.info("Logging configured — log dir: %s", log_dir)
