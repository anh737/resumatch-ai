from fastapi import APIRouter

from handler.health import health_check
from schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(probe: bool = False) -> HealthResponse:
    return await health_check(probe=probe)
