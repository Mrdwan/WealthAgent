"""Unit tests for telegram_bot.py."""

from unittest import mock

import pytest


def test_telegram_bot_main():
    """main() sets up signal handler and enters sleep loop."""
    import telegram_bot

    with (
        mock.patch.object(telegram_bot.time, "sleep", side_effect=SystemExit(0)),
        pytest.raises(SystemExit),
    ):
        telegram_bot.main()
