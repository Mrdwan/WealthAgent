"""Unit tests for src/reports.py."""

from datetime import datetime, timedelta

from db import Report, get_conn
from reports import (
    count_reports,
    get_report,
    list_reports,
    purge_expired_reports,
    save_report,
)

# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------


def test_save_report_returns_id():
    report_id = save_report("rebalance", "Full content here.", summary="Short summary.")
    assert isinstance(report_id, int)
    assert report_id > 0


def test_save_report_creates_record():
    report_id = save_report(
        "rebalance", "Full content here.", ticker=None, summary="Short summary."
    )
    report = get_report(report_id)
    assert report is not None
    assert report.report_type == "rebalance"
    assert report.full_content == "Full content here."
    assert report.summary == "Short summary."
    assert report.ticker is None


def test_save_report_with_ticker():
    report_id = save_report(
        "analyze", "Detailed AAPL analysis.", ticker="AAPL", summary="AAPL looks good."
    )
    report = get_report(report_id)
    assert report is not None
    assert report.ticker == "AAPL"
    assert report.report_type == "analyze"


def test_save_report_sets_expires_at():
    report_id = save_report("rebalance", "Content.", summary="Summary.")
    report = get_report(report_id)
    assert report is not None
    assert report.expires_at > datetime.now()


# ---------------------------------------------------------------------------
# get_report
# ---------------------------------------------------------------------------


def test_get_report_returns_report():
    report_id = save_report("analyze", "Content.", ticker="MSFT", summary="Summary.")
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
    id1 = save_report("rebalance", "First report.", summary="S.")
    id2 = save_report("rebalance", "Second report.", summary="S.")
    id3 = save_report("analyze", "Third report.", ticker="GOOG", summary="S.")
    results = list_reports()
    assert len(results) == 3
    # Most recent first
    assert results[0].id == id3
    assert results[1].id == id2
    assert results[2].id == id1


def test_list_reports_pagination():
    for i in range(5):
        save_report("rebalance", f"Report {i}.", summary="S.")
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
    save_report("rebalance", "One.", summary="S.")
    save_report("analyze", "Two.", ticker="AAPL", summary="S.")
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
