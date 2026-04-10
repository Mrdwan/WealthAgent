"""Unit tests for run_pipeline.py."""

import sys
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# _setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging(tmp_path, monkeypatch):
    import run_pipeline

    monkeypatch.setattr(run_pipeline.settings, "log_dir", tmp_path)
    run_pipeline._setup_logging()
    log_files = list(tmp_path.glob("pipeline_*.log"))
    assert len(log_files) == 1


# ---------------------------------------------------------------------------
# cmd_prices
# ---------------------------------------------------------------------------


def test_cmd_prices(monkeypatch):
    import run_pipeline

    mock_fetch = mock.MagicMock()
    mod = mock.MagicMock(fetch_all_prices=mock_fetch)
    with mock.patch.dict("sys.modules", {"price_fetcher": mod}):
        run_pipeline.cmd_prices()
    mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_hourly
# ---------------------------------------------------------------------------


def test_cmd_hourly(monkeypatch):
    import run_pipeline

    mock_feeds = mock.MagicMock()
    mock_process = mock.MagicMock(return_value=0)
    mock_checks = mock.MagicMock(return_value=[])
    mock_send = mock.MagicMock()

    mods = {
        "news_fetcher": mock.MagicMock(fetch_all_feeds=mock_feeds),
        "news_extractor": mock.MagicMock(process_unprocessed=mock_process),
        "alert_engine": mock.MagicMock(run_all_checks=mock_checks),
        "notifier": mock.MagicMock(send_alert=mock_send),
    }
    with mock.patch.dict("sys.modules", mods):
        run_pipeline.cmd_hourly()

    mock_feeds.assert_called_once()
    mock_process.assert_called_once()
    mock_checks.assert_called_once()


def test_cmd_hourly_with_alerts(monkeypatch):
    import run_pipeline

    alert = mock.MagicMock()
    mock_send = mock.MagicMock()

    mods = {
        "news_fetcher": mock.MagicMock(fetch_all_feeds=mock.MagicMock()),
        "news_extractor": mock.MagicMock(process_unprocessed=mock.MagicMock(return_value=0)),
        "alert_engine": mock.MagicMock(run_all_checks=mock.MagicMock(return_value=[alert])),
        "notifier": mock.MagicMock(send_alert=mock_send),
    }
    with mock.patch.dict("sys.modules", mods):
        run_pipeline.cmd_hourly()

    mock_send.assert_called_once_with(alert)


def test_cmd_hourly_alert_send_failure(monkeypatch):
    import run_pipeline

    alert = mock.MagicMock()

    mods = {
        "news_fetcher": mock.MagicMock(fetch_all_feeds=mock.MagicMock()),
        "news_extractor": mock.MagicMock(process_unprocessed=mock.MagicMock(return_value=0)),
        "alert_engine": mock.MagicMock(run_all_checks=mock.MagicMock(return_value=[alert])),
        "notifier": mock.MagicMock(send_alert=mock.MagicMock(side_effect=Exception("send failed"))),
    }
    with mock.patch.dict("sys.modules", mods):
        # Should not raise — error is logged
        run_pipeline.cmd_hourly()


# ---------------------------------------------------------------------------
# cmd_daily
# ---------------------------------------------------------------------------


def test_cmd_daily(monkeypatch):
    import run_pipeline

    mock_ecb = mock.MagicMock()
    monkeypatch.setattr(run_pipeline, "cmd_prices", mock.MagicMock())
    monkeypatch.setattr(run_pipeline, "cmd_hourly", mock.MagicMock())

    with mock.patch.dict("sys.modules", {"fx_fetcher": mock.MagicMock(fetch_ecb_rates=mock_ecb)}):
        run_pipeline.cmd_daily()

    mock_ecb.assert_called_once()
    run_pipeline.cmd_prices.assert_called_once()
    run_pipeline.cmd_hourly.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_weekly
# ---------------------------------------------------------------------------


def test_cmd_weekly(monkeypatch):
    import run_pipeline

    mock_fund = mock.MagicMock()
    monkeypatch.setattr(run_pipeline, "cmd_daily", mock.MagicMock())

    mod = mock.MagicMock(fetch_all_fundamentals=mock_fund)
    with mock.patch.dict("sys.modules", {"fundamentals": mod}):
        run_pipeline.cmd_weekly()

    run_pipeline.cmd_daily.assert_called_once()
    mock_fund.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_monthly
# ---------------------------------------------------------------------------


def test_cmd_monthly(monkeypatch):
    import run_pipeline

    mock_screen = mock.MagicMock(return_value=5)
    monkeypatch.setattr(run_pipeline, "cmd_weekly", mock.MagicMock())

    mod = mock.MagicMock(run_monthly_screen=mock_screen)
    with mock.patch.dict("sys.modules", {"screener": mod}):
        run_pipeline.cmd_monthly()

    run_pipeline.cmd_weekly.assert_called_once()
    mock_screen.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_rebalance
# ---------------------------------------------------------------------------


def test_cmd_rebalance(monkeypatch, capsys):
    import run_pipeline

    mods = {
        "advisor": mock.MagicMock(monthly_rebalance=mock.MagicMock(return_value="Hold AAPL")),
        "notifier": mock.MagicMock(send_message=mock.MagicMock()),
    }
    with mock.patch.dict("sys.modules", mods):
        run_pipeline.cmd_rebalance()

    out = capsys.readouterr().out
    assert "Hold AAPL" in out


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def test_cmd_status(monkeypatch, capsys):
    import run_pipeline

    summary_mock = mock.MagicMock(return_value="Portfolio: €1000")
    mods = {
        "context_builder": mock.MagicMock(build_holdings_summary=summary_mock),
        "notifier": mock.MagicMock(send_message=mock.MagicMock()),
    }
    with mock.patch.dict("sys.modules", mods):
        run_pipeline.cmd_status()

    out = capsys.readouterr().out
    assert "Portfolio: €1000" in out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_valid_command(monkeypatch, tmp_path):
    import run_pipeline

    monkeypatch.setattr(sys, "argv", ["run_pipeline", "prices"])
    monkeypatch.setattr(run_pipeline.settings, "log_dir", tmp_path)
    monkeypatch.setattr(run_pipeline, "cmd_prices", mock.MagicMock())
    run_pipeline.main()
    run_pipeline.cmd_prices.assert_called_once()


def test_main_no_args(monkeypatch, tmp_path):
    import run_pipeline

    monkeypatch.setattr(sys, "argv", ["run_pipeline"])
    monkeypatch.setattr(run_pipeline.settings, "log_dir", tmp_path)
    with pytest.raises(SystemExit, match="1"):
        run_pipeline.main()


def test_main_invalid_command(monkeypatch, tmp_path):
    import run_pipeline

    monkeypatch.setattr(sys, "argv", ["run_pipeline", "invalid"])
    monkeypatch.setattr(run_pipeline.settings, "log_dir", tmp_path)
    with pytest.raises(SystemExit, match="1"):
        run_pipeline.main()


def test_main_command_failure(monkeypatch, tmp_path):
    import run_pipeline

    monkeypatch.setattr(sys, "argv", ["run_pipeline", "prices"])
    monkeypatch.setattr(run_pipeline.settings, "log_dir", tmp_path)
    monkeypatch.setattr(
        run_pipeline,
        "cmd_prices",
        mock.MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(SystemExit, match="1"):
        run_pipeline.main()
