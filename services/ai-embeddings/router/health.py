from fastapi import APIRouter

from handler.health import health_check
from schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return health_check()
