"""Unit tests for log_setup.py."""

import logging
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _restore_root(saved_handlers: list, saved_level: int) -> None:
    """Restore the root logger to its original state after a test."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    root.handlers.clear()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


# ---------------------------------------------------------------------------
# DailyLogHandler
# ---------------------------------------------------------------------------


def test_daily_log_handler_creates_todays_file(tmp_path):
    """Handler opens a file named with today's date (DD-MM-YYYY.log)."""
    from log_setup import DailyLogHandler

    handler = DailyLogHandler(tmp_path)
    try:
        expected = date.today().strftime("%d-%m-%Y.log")
        assert Path(handler.baseFilename).name == expected
    finally:
        handler.close()


def test_daily_log_handler_no_rollover_same_day(tmp_path):
    """shouldRollover returns 0 when the date has not changed."""
    from log_setup import DailyLogHandler

    handler = DailyLogHandler(tmp_path)
    try:
        assert handler.shouldRollover(mock.MagicMock()) == 0
    finally:
        handler.close()


def test_daily_log_handler_rollover_on_new_day(tmp_path):
    """shouldRollover returns 1 when the calendar date has advanced."""
    from log_setup import DailyLogHandler

    with mock.patch("log_setup.date") as mock_date_cls:
        mock_date_cls.today.return_value = date(2026, 4, 10)
        handler = DailyLogHandler(tmp_path)

    try:
        with mock.patch("log_setup.date") as mock_date_cls:
            mock_date_cls.today.return_value = date(2026, 4, 11)
            assert handler.shouldRollover(mock.MagicMock()) == 1
    finally:
        handler.close()


def test_daily_log_handler_do_rollover_switches_file(tmp_path):
    """doRollover opens a new file named with the new date."""
    from log_setup import DailyLogHandler

    with mock.patch("log_setup.date") as mock_date_cls:
        mock_date_cls.today.return_value = date(2026, 4, 10)
        handler = DailyLogHandler(tmp_path)

    try:
        with mock.patch("log_setup.date") as mock_date_cls:
            mock_date_cls.today.return_value = date(2026, 4, 11)
            handler.doRollover()
        assert Path(handler.baseFilename).name == "11-04-2026.log"
    finally:
        handler.close()


def test_daily_log_handler_do_rollover_with_closed_stream(tmp_path):
    """doRollover works correctly even when the stream is already closed."""
    from log_setup import DailyLogHandler

    with mock.patch("log_setup.date") as mock_date_cls:
        mock_date_cls.today.return_value = date(2026, 4, 10)
        handler = DailyLogHandler(tmp_path)

    # Manually close and null the stream as if someone already closed it
    handler.stream.close()
    handler.stream = None  # type: ignore[assignment]

    try:
        with mock.patch("log_setup.date") as mock_date_cls:
            mock_date_cls.today.return_value = date(2026, 4, 11)
            handler.doRollover()
        assert Path(handler.baseFilename).name == "11-04-2026.log"
    finally:
        handler.close()


# ---------------------------------------------------------------------------
# _parse_log_date
# ---------------------------------------------------------------------------


def test_parse_log_date_valid():
    """Parses a correctly formatted DD-MM-YYYY.log filename."""
    from log_setup import _parse_log_date

    assert _parse_log_date("10-04-2026.log") == date(2026, 4, 10)


def test_parse_log_date_first_of_month():
    """Parses a date with single-digit day/month fields (zero-padded)."""
    from log_setup import _parse_log_date

    assert _parse_log_date("01-01-2026.log") == date(2026, 1, 1)


def test_parse_log_date_non_matching_filename():
    """Returns None for filenames that do not match the pattern."""
    from log_setup import _parse_log_date

    assert _parse_log_date("wealthagent.log") is None
    assert _parse_log_date("2026-04-10.log") is None
    assert _parse_log_date("logfile.txt") is None


def test_parse_log_date_invalid_calendar_values():
    """Returns None when parsed values do not form a valid date."""
    from log_setup import _parse_log_date

    assert _parse_log_date("32-13-2026.log") is None


# ---------------------------------------------------------------------------
# purge_old_logs
# ---------------------------------------------------------------------------


def test_purge_old_logs_deletes_previous_month(tmp_path):
    """Deletes log files from months before the current month."""
    from log_setup import purge_old_logs

    today = date.today()
    if today.month == 1:
        old_date = date(today.year - 1, 12, 15)
    else:
        old_date = date(today.year, today.month - 1, 15)

    old_log = tmp_path / old_date.strftime("%d-%m-%Y.log")
    current_log = tmp_path / today.strftime("%d-%m-%Y.log")
    old_log.write_text("old")
    current_log.write_text("current")

    purge_old_logs(tmp_path)

    assert not old_log.exists()
    assert current_log.exists()


def test_purge_old_logs_keeps_unmatched_files(tmp_path):
    """Leaves files that do not match the DD-MM-YYYY.log pattern untouched."""
    from log_setup import purge_old_logs

    other = tmp_path / "wealthagent.log"
    other.write_text("active")

    purge_old_logs(tmp_path)

    assert other.exists()


def test_purge_old_logs_nonexistent_dir(tmp_path):
    """Does not raise when the log directory does not exist."""
    from log_setup import purge_old_logs

    purge_old_logs(tmp_path / "nonexistent")


def test_purge_old_logs_keeps_current_month(tmp_path):
    """Does not delete logs from the current month."""
    from log_setup import purge_old_logs

    today = date.today()
    current_log = tmp_path / today.strftime("%d-%m-%Y.log")
    current_log.write_text("today")

    purge_old_logs(tmp_path)

    assert current_log.exists()


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_root():
    """Save and restore the root logger state around a test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield root
    _restore_root(saved_handlers, saved_level)


def test_setup_logging_configures_two_handlers(tmp_path, isolated_root):
    """setup_logging installs exactly one console and one file handler."""
    from log_setup import setup_logging

    setup_logging(tmp_path)

    assert isolated_root.level == logging.INFO
    assert len(isolated_root.handlers) == 2


def test_setup_logging_creates_log_dir(tmp_path, isolated_root):
    """setup_logging creates the log directory if it does not exist."""
    from log_setup import setup_logging

    log_dir = tmp_path / "nested" / "logs"
    setup_logging(log_dir)

    assert log_dir.exists()


def test_setup_logging_clears_existing_handlers(tmp_path, isolated_root):
    """setup_logging replaces any pre-existing handlers on the root logger."""
    from log_setup import setup_logging

    # Pre-install a dummy handler
    dummy = logging.NullHandler()
    isolated_root.addHandler(dummy)

    setup_logging(tmp_path)

    assert dummy not in isolated_root.handlers
    assert len(isolated_root.handlers) == 2


def test_setup_logging_purges_old_logs(tmp_path, isolated_root):
    """setup_logging deletes log files from previous months."""
    from log_setup import setup_logging

    today = date.today()
    if today.month == 1:
        old_date = date(today.year - 1, 12, 1)
    else:
        old_date = date(today.year, today.month - 1, 1)

    old_log = tmp_path / old_date.strftime("%d-%m-%Y.log")
    old_log.write_text("stale")

    setup_logging(tmp_path)

    assert not old_log.exists()
