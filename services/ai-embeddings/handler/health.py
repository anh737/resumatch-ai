from schemas.health import HealthResponse
from setting import settings


def health_check() -> HealthResponse:
    return HealthResponse(app=settings.APP_NAME)
