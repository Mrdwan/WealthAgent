"""Report list and detail routes for the WealthAgent dashboard."""

import markdown as md
from fastapi import APIRouter, Depends, HTTPException, Request

from dashboard.auth import require_auth
from reports import count_reports, get_report, list_reports

router = APIRouter(prefix="/reports", dependencies=[Depends(require_auth)])

_PER_PAGE = 20


@router.get("")
async def list_reports_page(request: Request, page: int = 1):
    """Render the reports list page."""
    offset = (page - 1) * _PER_PAGE
    report_list = list_reports(limit=_PER_PAGE, offset=offset)
    total = count_reports()
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {
            "reports": report_list,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


@router.get("/{report_id}")
async def report_detail(request: Request, report_id: int):
    """Render the report detail page."""
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    content_html = md.markdown(report.full_content, extensions=["fenced_code", "nl2br"])
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "reports/detail.html",
        {
            "report": report,
            "content_html": content_html,
        },
    )
