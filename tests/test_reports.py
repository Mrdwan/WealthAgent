"""Unit tests for src/reports.py."""

from datetime import datetime, timedelta
from unittest.mock import patch

from db import Report, get_conn
from reports import (
    count_reports,
    generate_summary,
    get_report,
    list_reports,
    purge_expired_reports,
    save_report,
)

# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------


def test_save_report_returns_id():
    with patch("reports.generate_summary", return_value="Short summary."):
        report_id = save_report("rebalance", "Full content here.")
    assert isinstance(report_id, int)
    assert report_id > 0


def test_save_report_creates_record():
    with patch("reports.generate_summary", return_value="Short summary."):
        report_id = save_report("rebalance", "Full content here.", ticker=None)
    report = get_report(report_id)
    assert report is not None
    assert report.report_type == "rebalance"
    assert report.full_content == "Full content here."
    assert report.summary == "Short summary."
    assert report.ticker is None


def test_save_report_with_ticker():
    with patch("reports.generate_summary", return_value="AAPL looks good."):
        report_id = save_report("analyze", "Detailed AAPL analysis.", ticker="AAPL")
    report = get_report(report_id)
    assert report is not None
    assert report.ticker == "AAPL"
    assert report.report_type == "analyze"


def test_save_report_sets_expires_at():
    with patch("reports.generate_summary", return_value="Summary."):
        report_id = save_report("rebalance", "Content.")
    report = get_report(report_id)
    assert report is not None
    assert report.expires_at > datetime.now()


# ---------------------------------------------------------------------------
# get_report
# ---------------------------------------------------------------------------


def test_get_report_returns_report():
    with patch("reports.generate_summary", return_value="Summary."):
        report_id = save_report("analyze", "Content.", ticker="MSFT")
    result = get_report(report_id)
    assert isinstance(result, Report)
    assert result.id == report_id


def test_get_report_not_found():
    result = get_report(99999)
    assert result is None


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------


def test_list_reports_empty():
    results = list_reports()
    assert results == []


def test_list_reports_ordered_desc():
    with patch("reports.generate_summary", return_value="S."):
        id1 = save_report("rebalance", "First report.")
        id2 = save_report("rebalance", "Second report.")
        id3 = save_report("analyze", "Third report.", ticker="GOOG")
    results = list_reports()
    assert len(results) == 3
    # Most recent first
    assert results[0].id == id3
    assert results[1].id == id2
    assert results[2].id == id1


def test_list_reports_pagination():
    with patch("reports.generate_summary", return_value="S."):
        for i in range(5):
            save_report("rebalance", f"Report {i}.")
    page1 = list_reports(limit=2, offset=0)
    page2 = list_reports(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # Pages should not overlap
    ids_page1 = {r.id for r in page1}
    ids_page2 = {r.id for r in page2}
    assert ids_page1.isdisjoint(ids_page2)


# ---------------------------------------------------------------------------
# count_reports
# ---------------------------------------------------------------------------


def test_count_reports():
    assert count_reports() == 0
    with patch("reports.generate_summary", return_value="S."):
        save_report("rebalance", "One.")
        save_report("analyze", "Two.", ticker="AAPL")
    assert count_reports() == 2


# ---------------------------------------------------------------------------
# purge_expired_reports
# ---------------------------------------------------------------------------


def test_purge_expired_reports():
    conn = get_conn()
    try:
        past = datetime.now() - timedelta(days=1)
        future = datetime.now() + timedelta(days=90)
        conn.execute(
            "INSERT INTO reports (report_type, summary, full_content, expires_at)"
            " VALUES (?, ?, ?, ?)",
            ("rebalance", "old summary", "old content", past.isoformat()),
        )
        conn.execute(
            "INSERT INTO reports (report_type, summary, full_content, expires_at)"
            " VALUES (?, ?, ?, ?)",
            ("analyze", "valid summary", "valid content", future.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    deleted = purge_expired_reports()
    assert deleted == 1
    assert count_reports() == 1


def test_purge_expired_returns_count():
    conn = get_conn()
    try:
        past = datetime.now() - timedelta(days=1)
        for _ in range(3):
            conn.execute(
                "INSERT INTO reports (report_type, summary, full_content, expires_at)"
                " VALUES (?, ?, ?, ?)",
                ("rebalance", "summary", "content", past.isoformat()),
            )
        conn.commit()
    finally:
        conn.close()

    count = purge_expired_reports()
    assert count == 3


# ---------------------------------------------------------------------------
# generate_summary
# ---------------------------------------------------------------------------


def test_generate_summary_calls_ollama():
    mock_response = "This is a concise summary."
    target = "reports.ollama_client.post_chat_completion"
    with patch(target, return_value=mock_response) as mock_call:
        result = generate_summary("Full investment analysis content here.")
    assert result == mock_response
    mock_call.assert_called_once()
    call_payload = mock_call.call_args[0][0]
    assert call_payload["messages"][0]["role"] == "user"
    assert "Full investment analysis content here." in call_payload["messages"][0]["content"]


def test_generate_summary_fallback():
    long_content = "A" * 300
    with patch("reports.ollama_client.post_chat_completion", side_effect=Exception("Ollama down")):
        result = generate_summary(long_content)
    assert result == long_content[:200] + "…"


def test_generate_summary_short_fallback():
    short_content = "Short content."
    with patch("reports.ollama_client.post_chat_completion", side_effect=Exception("Ollama down")):
        result = generate_summary(short_content)
    assert result == short_content
    assert "…" not in result
