"""FastAPI application factory for the WealthAgent dashboard.

Serves a JSON API for the React SPA frontend.  The built React app is
served as static files at ``/`` (fallback to ``index.html`` for client-side
routing).  No authentication is required — the dashboard is local-network only.
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from dashboard.routes_alerts import router as alerts_router
from dashboard.routes_charts import router as charts_router
from dashboard.routes_iwda import router as iwda_router
from dashboard.routes_logs import router as logs_router
from dashboard.routes_purge import router as purge_router
from dashboard.routes_reports import router as reports_router

_SPA_DIR = Path(__file__).resolve().parent / "spa"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="WealthAgent Dashboard")

    # JSON API routers
    app.include_router(reports_router)
    app.include_router(logs_router)
    app.include_router(purge_router)
    app.include_router(charts_router)
    app.include_router(alerts_router)
    app.include_router(iwda_router)

    # Serve React SPA build (if it exists)
    if _SPA_DIR.exists():  # pragma: no cover
        app.mount("/assets", StaticFiles(directory=str(_SPA_DIR / "assets")), name="spa-assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            """Serve the React SPA — fallback all non-API routes to index.html."""
            file_path = _SPA_DIR / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(_SPA_DIR / "index.html")

    return app


def run_dashboard() -> None:
    """Start the uvicorn server. Called from entrypoint.py."""
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=settings.dashboard_port, log_level="info")
