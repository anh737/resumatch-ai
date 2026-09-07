import asyncio

from schemas.health import HealthResponse
from services.database import postgres
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from setting import settings


async def health_check(*, probe: bool = False) -> HealthResponse:
    """Liveness (default) or, with ``probe=true``, dependency readiness.

    ``langfuse=false`` covers both "keys not configured" and "unreachable"
    (the admin portal reads ``GET /admin/traces`` to tell the two apart).
    """
    if not probe:
        return HealthResponse(app=settings.APP_NAME)
    pg, kf, mn, lf = await asyncio.gather(postgres.ping(), kafka.ping(), minio.ping(), langfuse.ping())
    return HealthResponse(app=settings.APP_NAME, postgres=pg, kafka=kf, minio=mn, langfuse=lf)
