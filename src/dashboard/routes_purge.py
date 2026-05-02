"""Purge control API routes for the WealthAgent dashboard."""

import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.settings import settings
from reports import purge_expired_reports

router = APIRouter(prefix="/api/purge")

_LOG_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{4}\.log$")


def _parse_log_date(filename: str) -> date:
    """Parse date from DD-MM-YYYY.log filename."""
    parts = filename.removesuffix(".log").split("-")
    return date(int(parts[2]), int(parts[1]), int(parts[0]))


def _purge_logs(older_than_days: int) -> int:
    """Delete log files older than older_than_days days. Returns count deleted."""
    cutoff = date.today() - timedelta(days=older_than_days)
    log_dir: Path = settings.log_dir
    if not log_dir.exists():
        return 0
    count = 0
    for f in log_dir.iterdir():
        if f.is_file() and _LOG_PATTERN.match(f.name):
            try:
                log_date = _parse_log_date(f.name)
                if log_date < cutoff:
                    f.unlink()
                    count += 1
            except (ValueError, IndexError):
                continue
    return count


class PurgeLogsRequest(BaseModel):
    """Request body for log purge."""

    older_than_days: int


class PurgeDataRequest(BaseModel):
    """Request body for pipeline data purge."""

    news_days: int
    alerts_days: int


@router.post("/logs")
async def purge_logs_action(body: PurgeLogsRequest) -> JSONResponse:
    """Delete log files older than the specified number of days."""
    days = max(1, body.older_than_days)
    count = _purge_logs(days)
    return JSONResponse({"deleted": count, "type": "logs"})


@router.post("/reports")
async def purge_reports_action() -> JSONResponse:
    """Delete all expired reports."""
    count = purge_expired_reports()
    return JSONResponse({"deleted": count, "type": "reports"})


@router.post("/data")
async def purge_data_action(body: PurgeDataRequest) -> JSONResponse:
    """Delete old pipeline data (news, alerts, screener candidates, fundamentals)."""
    from purge import (  # noqa: PLC0415
        purge_old_alerts,
        purge_old_news,
    )

    news_days = max(1, body.news_days)
    alerts_days = max(1, body.alerts_days)

    counts = {
        "news": purge_old_news(news_days),
        "alerts": purge_old_alerts(alerts_days),
    }
    return JSONResponse({"deleted": counts, "total": sum(counts.values())})
