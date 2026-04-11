"""Unit tests for entrypoint.py."""

from unittest import mock


def test_entrypoint_main_dashboard_disabled():
    """main() skips dashboard subprocess when dashboard_enabled is False."""
    import entrypoint

    with (
        mock.patch.object(entrypoint.os, "execv") as mock_execv,
        mock.patch("log_setup.setup_logging"),
        mock.patch("subprocess.Popen") as mock_popen,
    ):
        entrypoint.settings.dashboard_enabled = False
        try:
            entrypoint.main()
        finally:
            entrypoint.settings.dashboard_enabled = True  # restore default

    mock_execv.assert_called_once()
    args = mock_execv.call_args[0]
    assert "telegram_bot.py" in args[1][1]
    mock_popen.assert_not_called()


def test_entrypoint_main_dashboard_enabled():
    """main() starts dashboard subprocess when dashboard_enabled is True."""
    import entrypoint

    with (
        mock.patch.object(entrypoint.os, "execv"),
        mock.patch("log_setup.setup_logging"),
        mock.patch("subprocess.Popen") as mock_popen,
    ):
        entrypoint.settings.dashboard_enabled = True
        entrypoint.settings.dashboard_port = 8080
        entrypoint.main()

    mock_popen.assert_called_once()
    popen_args = mock_popen.call_args[0][0]  # positional first arg = cmd list
    assert "run_dashboard" in " ".join(popen_args)
