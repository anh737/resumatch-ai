from fastapi import APIRouter

from handler.health import health_check
from schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health(probe: bool = False) -> HealthResponse:
    return await health_check(probe=probe)
