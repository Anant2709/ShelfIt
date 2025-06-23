from fastapi import FastAPI
from app.api import health
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import auth

# Instantiate the web app
app = FastAPI(
    title="ShelfIt",
    version="0.0.1",
    docs_url="/docs"
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])


# configure CORS (Cross-Origin Resource Sharing) middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the health router under /api/health
app.include_router(health.router, prefix="/api/health", tags=["health"])
