"""Serve the built React app from the same origin as the API.

Local development keeps Vite on :5173. A hosted one-URL deploy copies
`frontend/dist` into `STATIC_DIR` and this module hands the browser those
files so the session cookie stays first-party.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


def mount_frontend(app: FastAPI) -> None:
    root = _static_root()
    if root is None:
        return

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Trailing-slash API paths must 307/404, not return index.html.
        first = full_path.split("/", 1)[0]
        if first in {"api", "health", "docs", "redoc", "openapi.json"}:
            raise HTTPException(status_code=404, detail="Not found")
        existing = _safe_file(root, full_path)
        if existing is not None:
            return FileResponse(existing)
        return FileResponse(root / "index.html")
