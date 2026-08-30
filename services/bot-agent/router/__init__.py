"""router — API route declarations (FastAPI APIRouter), endpoint -> handler mapping only."""

from fastapi import APIRouter

from .chat import router as chat_router
from .health import router as health_router
from .history import router as history_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
api_router.include_router(history_router)

__all__ = ["api_router"]
