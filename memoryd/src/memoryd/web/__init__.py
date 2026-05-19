"""FastAPI app factory for memoryd browse-only dashboard."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


def _module_dir() -> Path:
    return Path(__file__).parent


def create_app(token: str, data_root: Path) -> FastAPI:
    """Create FastAPI app bound to the given token + data root."""
    app = FastAPI(title="memoryd web", docs_url=None, redoc_url=None)
    app.state.token = token
    app.state.data_root = data_root
    templates_dir = _module_dir() / "templates"
    static_dir = _module_dir() / "static"
    templates_dir.mkdir(exist_ok=True)
    static_dir.mkdir(exist_ok=True)
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def _check_token(request: Request, call_next):
        path = request.url.path
        if path == "/healthz" or path.startswith("/static"):
            return await call_next(request)
        supplied = (
            request.query_params.get("token")
            or request.cookies.get("memoryd_token")
            or (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        )
        if not supplied or not secrets.compare_digest(supplied, app.state.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    from .routes import router
    app.include_router(router)

    return app
