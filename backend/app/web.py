"""Serve the built React app from the same origin as the API.

Local development keeps Vite on :5173. A hosted one-URL deploy copies
`frontend/dist` into `STATIC_DIR` and this module hands the browser those
files so the session cookie stays first-party.

A catch-all GET `/{path}` is the wrong tool here: FastAPI would then own
that path for every method, so `POST /api/inventory/scan/` became 405
instead of reaching the scanner. Unmatched GET pages fall through as 404
and this module turns those into `index.html`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings


def _static_root() -> Path | None:
    root = Path(settings.static_dir).resolve()
    if (root / "index.html").is_file():
        return root
    return None


def _safe_file(root: Path, full_path: str) -> Path | None:
    candidate = (root / full_path).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


def _is_app_page(path: str) -> bool:
    first = path.lstrip("/").split("/", 1)[0]
    return first not in {
        "api",
        "health",
        "docs",
        "redoc",
        "openapi.json",
        "assets",
    }


def mount_frontend(app: FastAPI) -> None:
    root = _static_root()
    if root is None:
        return

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.exception_handler(StarletteHTTPException)
    async def spa_or_http_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if exc.status_code == 404 and request.method == "GET" and _is_app_page(path):
            existing = _safe_file(root, path.lstrip("/"))
            if existing is not None:
                return FileResponse(existing)
            return FileResponse(root / "index.html")
        if isinstance(exc, HTTPException):
            return await http_exception_handler(request, exc)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
