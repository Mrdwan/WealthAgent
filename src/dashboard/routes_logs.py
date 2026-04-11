"""Log file viewer routes for the WealthAgent dashboard."""

import html
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from config.settings import settings
from dashboard.auth import require_auth

router = APIRouter(prefix="/logs", dependencies=[Depends(require_auth)])

_LOG_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{4}\.log$")


def _parse_log_date(filename: str) -> date:
    """Parse date from DD-MM-YYYY.log filename."""
    parts = filename.removesuffix(".log").split("-")
    return date(int(parts[2]), int(parts[1]), int(parts[0]))


def _list_log_files() -> list[dict]:
    """Return log files sorted by date descending."""
    log_dir: Path = settings.log_dir
    if not log_dir.exists():
        return []
    files: list[dict] = []
    for f in log_dir.iterdir():
        if f.is_file() and _LOG_PATTERN.match(f.name):
            try:
                log_date = _parse_log_date(f.name)
                files.append(
                    {
                        "filename": f.name,
                        "date": log_date,
                        "size_kb": round(f.stat().st_size / 1024, 1),
                    }
                )
            except (ValueError, IndexError):
                continue
    return sorted(files, key=lambda x: x["date"], reverse=True)


@router.get("")
async def list_logs(request: Request):
    """Render the log files list page."""
    files = _list_log_files()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "logs/list.html",
        {"log_files": files},
    )


@router.get("/{filename}")
async def view_log(request: Request, filename: str):
    """Render a single log file's contents."""
    if not _LOG_PATTERN.match(filename):
        raise HTTPException(status_code=404, detail="Log file not found")
    log_path: Path = settings.log_dir / filename
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    content = log_path.read_text(encoding="utf-8", errors="replace")
    escaped = html.escape(content)
    lines = escaped.splitlines()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "logs/view.html",
        {
            "filename": filename,
            "lines": lines,
            "line_count": len(lines),
        },
    )
