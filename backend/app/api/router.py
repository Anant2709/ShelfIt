from fastapi import APIRouter

from app.api.endpoints import analytics, auth, chat, diet, inventory

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"], prefix="/auth")
api_router.include_router(inventory.router, tags=["inventory"], prefix="/inventory")
api_router.include_router(chat.router, tags=["chat"], prefix="/chat")
api_router.include_router(analytics.router, tags=["analytics"], prefix="/analytics")
api_router.include_router(diet.router, tags=["diet"], prefix="/diet")
