import asyncio

from schemas.health import HealthResponse
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings


async def health_check(*, probe: bool = False) -> HealthResponse:
    """Liveness (default) or full dependency probe (``?probe=true``).

    ``langfuse=false`` covers both "unreachable" and "disabled/unconfigured".
    """
    if not probe:
        return HealthResponse(app=settings.APP_NAME)
    return HealthResponse(
        app=settings.APP_NAME,
        qdrant=await qdrant.ping(),
        kafka=await kafka.ping(),
        minio=await minio.ping(),
        langfuse=await asyncio.to_thread(langfuse.auth_check),
    )
