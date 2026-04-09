"""Alert engine — checks for conditions requiring attention and logs them.

Checks:
  - price_drop:   held ticker dropped >N% in the last 30 days
  - news_signal:  negative signal for held ticker with confidence >= 0.6
  - opportunity:  positive signal for non-held ticker with confidence >= 0.7

CLI usage (inside the container):
    python -m alert_engine
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from db import db_conn, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_ALERT_DROP_PCT: float = float(os.environ.get("ALERT_DROP_PCT", "10.0"))


# ---------------------------------------------------------------------------
# Alert model
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    """A triggered alert."""

    type: str  # price_drop | news_signal | opportunity
    ticker: str | None = None
    details: dict[str, Any]
    triggered_at: datetime


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------


def _get_held_tickers() -> list[str]:
    """Return all non-bond tickers currently in the portfolio."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM holdings WHERE pool != 'bond'").fetchall()
    finally:
        conn.close()
    return [row["ticker"] for row in rows]


# ---------------------------------------------------------------------------
# Check: price drops
# ---------------------------------------------------------------------------


def check_price_drops(threshold_pct: float | None = None) -> list[Alert]:
    """Check for held tickers that have dropped more than threshold_pct in 30 days.

    Args:
        threshold_pct: Drop percentage trigger (default: ALERT_DROP_PCT env var).

    Returns:
        List of Alert objects for each ticker exceeding the threshold.
    """
    threshold = threshold_pct if threshold_pct is not None else _ALERT_DROP_PCT
    tickers = _get_held_tickers()
    alerts: list[Alert] = []

    if not tickers:
        log.info("No non-bond holdings — skipping price drop check.")
        return []

    conn = get_conn()
    try:
        for ticker in tickers:
            # Get current price (most recent)
            current_row = conn.execute(
                "SELECT close_eur, date FROM price_history"
                " WHERE ticker = ? AND close_eur IS NOT NULL"
                " ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()

            if current_row is None:
                log.debug("No price data for %s — skipping", ticker)
                continue

            current_price = current_row["close_eur"]
            current_date = current_row["date"]

            # Get price ~30 days ago (nearest available on or before)
            cutoff = (datetime.now(tz=UTC) - timedelta(days=30)).date().isoformat()
            prior_row = conn.execute(
                "SELECT close_eur, date FROM price_history"
                " WHERE ticker = ? AND close_eur IS NOT NULL AND date <= ?"
                " ORDER BY date DESC LIMIT 1",
                (ticker, cutoff),
            ).fetchone()

            if prior_row is None:
                log.debug("No 30-day-old price for %s — skipping", ticker)
                continue

            prior_price = prior_row["close_eur"]
            if prior_price == 0:
                continue

            drop_pct = ((current_price - prior_price) / prior_price) * 100.0

            if drop_pct <= -threshold:
                log.warning(
                    "%s dropped %.1f%% (€%.2f → €%.2f)",
                    ticker,
                    drop_pct,
                    prior_price,
                    current_price,
                )
                alerts.append(
                    Alert(
                        type="price_drop",
                        ticker=ticker,
                        details={
                            "drop_pct": round(drop_pct, 2),
                            "current_price_eur": round(current_price, 4),
                            "prior_price_eur": round(prior_price, 4),
                            "current_date": current_date,
                            "prior_date": prior_row["date"],
                            "threshold_pct": threshold,
                        },
                        triggered_at=datetime.now(tz=UTC),
                    )
                )
    finally:
        conn.close()

    log.info("Price drop check: %d alert(s) triggered", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# Check: news signals
# ---------------------------------------------------------------------------


def check_news_signals(hours: int = 24) -> list[Alert]:
    """Check for recent negative signals on held tickers.

    Requires: signal confidence >= 0.6 AND sentiment = 'negative' AND ticker
    in portfolio.

    Args:
        hours: Look-back window for signals.

    Returns:
        List of Alert objects.
    """
    held = set(_get_held_tickers())
    if not held:
        return []

    cutoff = (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM news_signals"
            " WHERE extracted_at >= ? AND confidence >= 0.6 AND sentiment = 'negative'",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    alerts: list[Alert] = []
    for row in rows:
        try:
            signal_tickers = json.loads(row["tickers"] or "[]")
        except (json.JSONDecodeError, TypeError):
            signal_tickers = []

        overlap = {t.upper() for t in signal_tickers} & {t.upper() for t in held}
        if not overlap:
            continue

        for ticker in overlap:
            alerts.append(
                Alert(
                    type="news_signal",
                    ticker=ticker,
                    details={
                        "sentiment": row["sentiment"],
                        "catalyst": row["catalyst"],
                        "timeframe": row["timeframe"],
                        "summary": row["summary"],
                        "confidence": row["confidence"],
                        "signal_id": row["id"],
                        "article_id": row["article_id"],
                    },
                    triggered_at=datetime.now(tz=UTC),
                )
            )

    log.info("News signal check: %d alert(s) triggered", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# Check: opportunities
# ---------------------------------------------------------------------------


def check_opportunities(hours: int = 24) -> list[Alert]:
    """Check for positive signals on tickers NOT currently held.

    Requires: confidence >= 0.7 AND sentiment = 'positive'.

    Args:
        hours: Look-back window for signals.

    Returns:
        List of Alert objects.
    """
    held = {t.upper() for t in _get_held_tickers()}

    cutoff = (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM news_signals"
            " WHERE extracted_at >= ? AND confidence >= 0.7 AND sentiment = 'positive'",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    alerts: list[Alert] = []
    for row in rows:
        try:
            signal_tickers = json.loads(row["tickers"] or "[]")
        except (json.JSONDecodeError, TypeError):
            signal_tickers = []

        non_held = {t.upper() for t in signal_tickers} - held
        if not non_held:
            continue

        for ticker in non_held:
            alerts.append(
                Alert(
                    type="opportunity",
                    ticker=ticker,
                    details={
                        "sentiment": row["sentiment"],
                        "catalyst": row["catalyst"],
                        "timeframe": row["timeframe"],
                        "summary": row["summary"],
                        "confidence": row["confidence"],
                        "signal_id": row["id"],
                        "article_id": row["article_id"],
                    },
                    triggered_at=datetime.now(tz=UTC),
                )
            )

    log.info("Opportunity check: %d alert(s) triggered", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# Log to DB
# ---------------------------------------------------------------------------


def _log_alert(alert: Alert) -> None:
    """Persist an alert to the alerts_log table."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type, details)"
            " VALUES (?, ?, ?, ?)",
            (
                alert.triggered_at.isoformat(),
                alert.ticker,
                alert.type,
                json.dumps(alert.details),
            ),
        )


# ---------------------------------------------------------------------------
# Combined run
# ---------------------------------------------------------------------------


def run_all_checks() -> list[Alert]:
    """Run all alert checks, deduplicate by ticker (first wins), and log to DB.

    Returns:
        Combined deduplicated list of Alert objects.
    """
    all_alerts: list[Alert] = []
    all_alerts.extend(check_price_drops())
    all_alerts.extend(check_news_signals())
    all_alerts.extend(check_opportunities())

    # Deduplicate: keep first alert per (type, ticker) pair
    seen: set[tuple[str, str | None]] = set()
    deduped: list[Alert] = []
    for alert in all_alerts:
        key = (alert.type, alert.ticker)
        if key not in seen:
            seen.add(key)
            deduped.append(alert)

    for alert in deduped:
        try:
            _log_alert(alert)
        except Exception as exc:
            log.error("Failed to log alert %s/%s: %s", alert.type, alert.ticker, exc)

    log.info(
        "Alert run complete: %d total, %d after dedup",
        len(all_alerts),
        len(deduped),
    )
    return deduped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all checks and print a summary."""
    alerts = run_all_checks()
    if not alerts:
        print("No alerts triggered.")
        return

    print(f"\n{len(alerts)} alert(s) triggered:")
    for a in alerts:
        print(f"  [{a.type.upper()}] {a.ticker or '—'}")
        for k, v in a.details.items():
            print(f"      {k}: {v}")


if __name__ == "__main__":
    main()
