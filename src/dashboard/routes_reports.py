"""Report list and detail API routes for the WealthAgent dashboard."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from reports import count_reports, get_report, list_reports

router = APIRouter(prefix="/api/reports")

_PER_PAGE = 20


@router.get("")
async def list_reports_endpoint(page: int = 1) -> JSONResponse:
    """Return paginated list of reports as JSON."""
    offset = (page - 1) * _PER_PAGE
    report_list = list_reports(limit=_PER_PAGE, offset=offset)
    total = count_reports()
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    return JSONResponse(
        {
            "reports": [
                {
                    "id": r.id,
                    "created_at": str(r.created_at),
                    "report_type": r.report_type,
                    "ticker": r.ticker,
                    "summary": r.summary,
                }
                for r in report_list
            ],
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }
    )


@router.get("/{report_id}")
async def report_detail(report_id: int) -> JSONResponse:
    """Return a single report as JSON (full_content included)."""
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return JSONResponse(
        {
            "id": report.id,
            "created_at": str(report.created_at),
            "report_type": report.report_type,
            "ticker": report.ticker,
            "summary": report.summary,
            "full_content": report.full_content,
        }
    )
