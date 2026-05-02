"""Alert engine — checks for conditions requiring attention and logs them.

Checks:
  - price_drop:   held ticker dropped >N% in the last 30 days (native USD)
  - iwda_exit:    held ticker has fallen out of IWDA's top-N hysteresis band

CLI usage (inside the container):
    python -m alert_engine
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from config.settings import settings
from db import db_conn, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert model
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    """A triggered alert."""

    type: str  # price_drop | iwda_exit
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
# Check: price drops (USD-native, avoids phantom EUR FX moves)
# ---------------------------------------------------------------------------


def check_price_drops(threshold_pct: float | None = None) -> list[Alert]:
    """Check for held tickers that have dropped more than threshold_pct in 30 days.

    Uses ``close_usd`` (native currency) to avoid phantom drops caused by EUR
    exchange-rate movements.

    Args:
        threshold_pct: Drop percentage trigger (default: ALERT_DROP_PCT env var).

    Returns:
        List of Alert objects for each ticker exceeding the threshold.
    """
    threshold = threshold_pct if threshold_pct is not None else settings.alert_drop_pct
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
                "SELECT close_usd, date FROM price_history"
                " WHERE ticker = ? AND close_usd IS NOT NULL"
                " ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()

            if current_row is None:
                log.debug("No price data for %s — skipping", ticker)
                continue

            current_price = current_row["close_usd"]
            current_date = current_row["date"]

            # Get price ~30 days ago (nearest available on or before)
            cutoff = (datetime.now(tz=UTC) - timedelta(days=30)).date().isoformat()
            prior_row = conn.execute(
                "SELECT close_usd, date FROM price_history"
                " WHERE ticker = ? AND close_usd IS NOT NULL AND date <= ?"
                " ORDER BY date DESC LIMIT 1",
                (ticker, cutoff),
            ).fetchone()

            if prior_row is None:
                log.debug("No 30-day-old price for %s — skipping", ticker)
                continue

            prior_price = prior_row["close_usd"]
            if prior_price == 0:
                continue

            drop_pct = ((current_price - prior_price) / prior_price) * 100.0

            if drop_pct <= -threshold:
                log.warning(
                    "%s dropped %.1f%% ($%.2f → $%.2f)",
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
                            "current_price_usd": round(current_price, 4),
                            "prior_price_usd": round(prior_price, 4),
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
# Check: IWDA exit — ticker fell out of top-N hysteresis band
# ---------------------------------------------------------------------------


def check_iwda_exits() -> list[Alert]:
    """Check for held tickers that have exited IWDA's top-N hysteresis band.

    Calls ``iwda_fetcher.compute_changes()`` and intersects its ``exited``
    list with the currently held tickers.  Returns ``[]`` if no IWDA snapshots
    exist yet (Phase 2 fetcher hasn't run).

    Returns:
        List of Alert objects, one per held ticker that exited the band.
    """
    from iwda_fetcher import compute_changes, most_recent_fetched_at

    if most_recent_fetched_at() is None:
        log.info("No IWDA snapshots available — skipping IWDA exit check.")
        return []

    changes = compute_changes()
    exited = changes.get("exited", [])
    if not exited:
        log.info("IWDA exit check: no exits detected.")
        return []

    held = {t.upper() for t in _get_held_tickers()}
    alerts: list[Alert] = []

    for item in exited:
        ticker = item["ticker"]
        if ticker.upper() not in held:
            continue
        alerts.append(
            Alert(
                type="iwda_exit",
                ticker=ticker,
                details={
                    "current_rank": item["current_rank"],
                    "prior_rank": item["prior_rank"],
                    "top_n": settings.iwda_top_n,
                    "exit_buffer": settings.iwda_exit_buffer,
                },
                triggered_at=datetime.now(tz=UTC),
            )
        )
        log.warning(
            "%s exited IWDA top-%d band (prior rank: %s, current rank: %s)",
            ticker,
            settings.iwda_top_n,
            item["prior_rank"],
            item["current_rank"],
        )

    log.info("IWDA exit check: %d alert(s) triggered", len(alerts))
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
    """Run all alert checks, deduplicate by (type, ticker) (first wins), log to DB.

    Returns:
        Combined deduplicated list of Alert objects.
    """
    all_alerts: list[Alert] = []
    all_alerts.extend(check_price_drops())
    all_alerts.extend(check_iwda_exits())

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
