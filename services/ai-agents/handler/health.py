import asyncio

from schemas.health import HealthResponse
from services.message_broker import kafka
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings


async def health_check(*, probe: bool = False) -> HealthResponse:
    """Liveness (default) or, with ``probe=true``, dependency readiness.

    ``langfuse`` is ``False`` both when tracing is disabled (missing keys) and
    when the configured keys are rejected — check the service log to tell the
    two apart. ``auth_check`` does blocking HTTP, hence the thread.
    """
    if not probe:
        return HealthResponse(app=settings.APP_NAME)
    return HealthResponse(
        app=settings.APP_NAME,
        qdrant=await qdrant.ping(),
        kafka=await kafka.ping(),
        langfuse=await asyncio.to_thread(langfuse.auth_check),
    )
