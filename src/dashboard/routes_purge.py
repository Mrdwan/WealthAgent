"""Purge control routes for the WealthAgent dashboard."""

import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request

from config.settings import settings
from dashboard.auth import require_auth
from reports import purge_expired_reports

router = APIRouter(prefix="/purge", dependencies=[Depends(require_auth)])

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


@router.get("")
async def purge_page(request: Request):
    """Render the purge controls page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "purge.html", {})


@router.post("/logs")
async def purge_logs_action(request: Request, older_than_days: int = Form(...)):
    """Delete log files older than the specified number of days."""
    if older_than_days < 1:
        older_than_days = 1
    count = _purge_logs(older_than_days)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "purge.html",
        {
            "log_result": f"Deleted {count} log file{'s' if count != 1 else ''}.",
        },
    )


@router.post("/reports")
async def purge_reports_action(request: Request):
    """Delete all expired reports."""
    count = purge_expired_reports()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "purge.html",
        {
            "report_result": f"Deleted {count} expired report{'s' if count != 1 else ''}.",
        },
    )
