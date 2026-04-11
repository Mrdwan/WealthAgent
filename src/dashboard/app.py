"""FastAPI application factory for the WealthAgent dashboard."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.settings import settings
from dashboard.auth import create_session_token, verify_password
from dashboard.routes_logs import router as logs_router
from dashboard.routes_reports import router as reports_router

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="WealthAgent Dashboard")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates

    @app.get("/")
    async def root() -> RedirectResponse:
        """Redirect root to /reports."""
        return RedirectResponse(url="/reports", status_code=302)

    @app.get("/login")
    async def get_login(request: Request):
        """Render the login page."""
        return templates.TemplateResponse(request, "login.html")

    @app.post("/login")
    async def post_login(request: Request, password: str = Form(...)):
        """Authenticate and set session cookie, or re-render login with error."""
        if verify_password(password):
            token = create_session_token()
            response = RedirectResponse(url="/reports", status_code=302)
            response.set_cookie("wa_session", token, httponly=True, samesite="lax")
            return response
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid password"},
            status_code=401,
        )

    @app.get("/logout")
    async def logout() -> RedirectResponse:
        """Clear session cookie and redirect to login."""
        response = RedirectResponse(url="/login", status_code=302)
        response.delete_cookie("wa_session")
        return response

    app.include_router(reports_router)
    app.include_router(logs_router)

    return app


def run_dashboard() -> None:
    """Start the uvicorn server. Called from entrypoint.py."""
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=settings.dashboard_port, log_level="info")
