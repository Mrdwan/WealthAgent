"""Unit tests for entrypoint.py."""

from unittest import mock


def test_entrypoint_main():
    """main() configures logging, calls init_db, then os.execv to launch the bot."""
    import entrypoint

    with (
        mock.patch.object(entrypoint.os, "execv") as mock_execv,
        mock.patch("log_setup.setup_logging"),
    ):
        entrypoint.main()
    mock_execv.assert_called_once()
    args = mock_execv.call_args[0]
    assert "telegram_bot.py" in args[1][1]
