from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.schema import log_schema_state
from app.db.session import engine
from app.web import mount_frontend

# The schema is owned by Alembic, not by this module. Startup reports whether the
# database is at the expected revision and leaves it alone otherwise; see
# app/db/schema.py for why creating or altering it here was the wrong default.
log_schema_state(engine)

app = FastAPI(title="Shelf It API")
app.include_router(api_router, prefix=settings.api_prefix)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


# After API routes and /health so `/api` and liveness stay real endpoints.
# No-op when frontend/dist is missing (local `uvicorn` + Vite).
mount_frontend(app)
