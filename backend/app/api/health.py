from fastapi import APIRouter

router = APIRouter()                     # create sub-router object

@router.get("/", summary="Health check") # GET  /api/health/
async def health():
    """Return service-status JSON for load-balancers or probes."""
    return {"status": "ok"}