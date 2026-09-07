from contextlib import asynccontextmanager

from fastapi import FastAPI

from handler.ingest import handle_upload_event
from router import api_router
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer_task = None
    try:
        await kafka.ensure_topics(
            [
                kafka.TOPIC_RESUME_UPLOADED,
                kafka.TOPIC_RESUME_PROCESSED,
                kafka.TOPIC_JOB_UPLOADED,
                kafka.TOPIC_JOB_PROCESSED,
                kafka.TOPIC_DEAD_LETTER,
            ]
        )
        consumer_task = kafka.start_consumer(
            [kafka.TOPIC_RESUME_UPLOADED, kafka.TOPIC_JOB_UPLOADED], handle_upload_event
        )
    except Exception as exc:  # noqa: BLE001 — /health must stay usable when the broker is down
        log.error("Kafka unavailable, ingestion pipeline disabled: %s", exc)
    yield
    if consumer_task is not None:
        await kafka.stop_consumer(consumer_task)
    await kafka.stop_producer()
    await qdrant.close_qdrant()
    minio.close_minio()
    langfuse.shutdown()


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=lifespan)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
