"""Report storage and retrieval for WealthAgent.

Handles persisting LLM advisor reports (rebalance and per-ticker analysis)
to SQLite and purging expired entries.
"""

import logging
from datetime import datetime, timedelta

from config.settings import settings
from db import Report, db_conn, get_conn

log = logging.getLogger(__name__)


def save_report(
    report_type: str,
    full_content: str,
    ticker: str | None = None,
    *,
    summary: str,
) -> int:
    """Save a report and return its id.

    Args:
        report_type: ``"rebalance"`` or ``"analyze"``.
        full_content: Full markdown report text.
        ticker: Ticker symbol for ``"analyze"`` reports; ``None`` for rebalance.
        summary: Short one-line summary for Telegram notifications.
    """
    expires_at = datetime.now() + timedelta(days=settings.report_retention_days)

    with db_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO reports (report_type, ticker, summary, full_content, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (report_type, ticker, summary, full_content, expires_at.isoformat()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_report(report_id: int) -> Report | None:
    """Return a report by id, or None if not found."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return Report(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        report_type=row["report_type"],
        ticker=row["ticker"],
        summary=row["summary"],
        full_content=row["full_content"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


def list_reports(limit: int = 20, offset: int = 0) -> list[Report]:
    """Return reports ordered by created_at DESC."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM reports ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()

    return [
        Report(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            report_type=row["report_type"],
            ticker=row["ticker"],
            summary=row["summary"],
            full_content=row["full_content"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )
        for row in rows
    ]


def count_reports() -> int:
    """Return total number of reports."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM reports").fetchone()
    finally:
        conn.close()
    return row[0]


def purge_expired_reports() -> int:
    """Delete reports where expires_at < now. Returns count deleted."""
    now = datetime.now().isoformat()
    with db_conn() as conn:
        cursor = conn.execute("DELETE FROM reports WHERE expires_at < ?", (now,))
        return cursor.rowcount
