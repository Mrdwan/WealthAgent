"""Data retention — purges stale rows from pipeline tables.

Retention periods default to 4× the context-builder look-back windows so
recent data is always available for analysis while old rows do not accumulate
indefinitely.

CLI usage (inside the container):
    python -m purge
"""

import logging
from datetime import datetime, timedelta

from config.settings import settings
from db import db_conn

log = logging.getLogger(__name__)


def purge_old_news(days: int | None = None) -> int:
    """Delete news signals and articles older than *days* days.

    Signals are deleted first. Articles are then deleted only when no
    remaining signals reference them, preserving FK integrity.

    Returns the total number of rows deleted.
    """
    retention = days if days is not None else settings.news_retention_days
    cutoff = (datetime.now() - timedelta(days=retention)).isoformat()
    deleted = 0
    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM news_signals WHERE extracted_at < ?",
            (cutoff,),
        )
        deleted += cur.rowcount
        cur = conn.execute(
            """
            DELETE FROM news_articles
            WHERE fetched_at < ?
              AND id NOT IN (SELECT DISTINCT article_id FROM news_signals)
            """,
            (cutoff,),
        )
        deleted += cur.rowcount
    log.info("purge_old_news: deleted %d rows (retention=%d days)", deleted, retention)
    return deleted


def purge_old_alerts(days: int | None = None) -> int:
    """Delete alert log entries older than *days* days.

    Returns the number of rows deleted.
    """
    retention = days if days is not None else settings.alerts_retention_days
    cutoff = (datetime.now() - timedelta(days=retention)).isoformat()
    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM alerts_log WHERE triggered_at < ?",
            (cutoff,),
        )
        deleted = cur.rowcount
    log.info("purge_old_alerts: deleted %d rows (retention=%d days)", deleted, retention)
    return deleted


def purge_all() -> dict[str, int]:
    """Run all pipeline purge functions with configured retention periods.

    Returns a mapping of data category to rows deleted.
    """
    return {
        "news": purge_old_news(),
        "alerts": purge_old_alerts(),
    }


def main() -> None:
    """Run all purge jobs and print a summary."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    counts = purge_all()
    total = sum(counts.values())
    print(f"Purge complete: {counts} ({total} total rows deleted)")


if __name__ == "__main__":
    main()
