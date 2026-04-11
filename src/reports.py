"""Report storage, retrieval, and Ollama-powered summarisation for WealthAgent.

Handles persisting LLM advisor reports (rebalance and per-ticker analysis)
to SQLite, generating concise summaries via Ollama, and purging expired entries.
"""

import logging
from datetime import datetime, timedelta

import ollama_client
from config.settings import settings
from db import Report, db_conn, get_conn

log = logging.getLogger(__name__)


def generate_summary(full_content: str) -> str:
    """Use Ollama to generate a 2-3 sentence summary of the report.

    Falls back to a truncated version of the content on any error.
    """
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Summarise this investment analysis in 2-3 concise sentences, "
                    "focusing on the key recommendation. Reply with only the summary.\n\n"
                    + full_content[:3000]
                ),
            }
        ],
    }
    try:
        return ollama_client.post_chat_completion(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ollama summary failed, falling back to truncation: %s", exc)
        if len(full_content) > 200:
            return full_content[:200] + "…"
        return full_content


def save_report(
    report_type: str,
    full_content: str,
    ticker: str | None = None,
) -> int:
    """Save a report and return its id.

    The summary is generated via Ollama before insertion.
    """
    summary = generate_summary(full_content)
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
