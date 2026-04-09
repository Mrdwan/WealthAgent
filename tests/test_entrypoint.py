"""Unit tests for entrypoint.py."""

from unittest import mock


def test_entrypoint_main():
    """main() calls init_db, validates settings, then os.execv."""
    import entrypoint

    with mock.patch.object(entrypoint.os, "execv") as mock_execv:
        entrypoint.main()
    mock_execv.assert_called_once()
    # Second arg should be [python_executable, path_to_telegram_bot.py]
    args = mock_execv.call_args[0]
    assert "telegram_bot.py" in args[1][1]
