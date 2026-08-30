from schemas.health import HealthResponse
from services.database import postgres
from services.message_broker import kafka
from setting import settings


async def health_check(*, probe: bool = False) -> HealthResponse:
    """Liveness (default) or, with ``probe=true``, dependency readiness."""
    if not probe:
        return HealthResponse(app=settings.APP_NAME)
    return HealthResponse(app=settings.APP_NAME, postgres=await postgres.ping(), kafka=await kafka.ping())
