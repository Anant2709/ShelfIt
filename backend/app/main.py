from fastapi import FastAPI
from app.api import health

# Instantiate the web app
app = FastAPI(
    title="ShelfIt",
    version="0.0.1",
    docs_url="/docs"
)

# Register the health router under /api/health
app.include_router(health.router, prefix="/api/health", tags=["health"])