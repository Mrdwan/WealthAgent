"""Alerts list and configuration routes for the WealthAgent dashboard."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from config.settings import settings
from dashboard.auth import require_auth
from db import db_conn, get_conn

router = APIRouter(dependencies=[Depends(require_auth)])


def _get_alert_config(key: str) -> str | None:
    """Return the configured value for key, or None if not set."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM alert_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def _set_alert_config(key: str, value: str) -> None:
    """Upsert a key-value pair in alert_config."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alert_config (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _list_alert_configs() -> dict[str, str]:
    """Return all alert config entries as a dict."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM alert_config").fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


@router.get("/alerts")
async def alerts_list(request: Request):
    """Show recent alerts from alerts_log (last 30 days), newest first, max 100."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts_log WHERE triggered_at >= ? ORDER BY triggered_at DESC LIMIT 100",
            (cutoff,),
        ).fetchall()
        alerts = [dict(r) for r in rows]
    finally:
        conn.close()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "alerts/list.html",
        {"alerts": alerts},
    )


@router.get("/alerts/config")
async def alerts_config(request: Request, saved: str = ""):
    """Show the alert configuration form."""
    configs = _list_alert_configs()
    current = {
        "alert_drop_pct": configs.get("alert_drop_pct", str(settings.alert_drop_pct)),
        "stop_loss_pct": configs.get("stop_loss_pct", str(settings.stop_loss_pct)),
        "dividend_yield_max": configs.get("dividend_yield_max", str(settings.dividend_yield_max)),
    }
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "alerts/config.html",
        {
            "config": current,
            "saved": bool(saved),
        },
    )


@router.post("/alerts/config")
async def update_alerts_config(
    request: Request,
    alert_drop_pct: str = Form(...),
    stop_loss_pct: str = Form(...),
    dividend_yield_max: str = Form(...),
):
    """Update alert thresholds and redirect to config page with success param."""
    _set_alert_config("alert_drop_pct", alert_drop_pct)
    _set_alert_config("stop_loss_pct", stop_loss_pct)
    _set_alert_config("dividend_yield_max", dividend_yield_max)
    return RedirectResponse(url="/alerts/config?saved=1", status_code=302)
