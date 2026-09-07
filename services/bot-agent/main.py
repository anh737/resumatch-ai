from contextlib import asynccontextmanager

from fastapi import FastAPI

from handler.admin import handle_ingest_processed
from handler.chat import handle_chat_response
from router import api_router
from services.database import postgres
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer_tasks = []
    try:
        await postgres.init_schema()
    except Exception as exc:  # noqa: BLE001 — /health must stay usable when the DB is down
        log.error("PostgreSQL unavailable, chat history disabled: %s", exc)
    try:
        await minio.ensure_bucket(settings.MINIO_RESUME_BUCKET)
        await minio.ensure_bucket(settings.MINIO_JOB_BUCKET)
    except Exception as exc:  # noqa: BLE001 — buckets are re-ensured on each upload
        log.error("MinIO unavailable, uploads will fail until it is back: %s", exc)
    try:
        await kafka.ensure_topics(
            [
                kafka.TOPIC_CHAT_REQUESTS,
                kafka.TOPIC_CHAT_RESPONSES,
                kafka.TOPIC_RESUME_UPLOADED,
                kafka.TOPIC_RESUME_PROCESSED,
                kafka.TOPIC_JOB_UPLOADED,
                kafka.TOPIC_JOB_PROCESSED,
                kafka.TOPIC_DEAD_LETTER,
            ]
        )
        consumer_tasks.append(kafka.start_consumer([kafka.TOPIC_CHAT_RESPONSES], handle_chat_response))
        # Own group: two consumers of one group must not subscribe to different topics.
        consumer_tasks.append(
            kafka.start_consumer(
                [kafka.TOPIC_RESUME_PROCESSED, kafka.TOPIC_JOB_PROCESSED],
                handle_ingest_processed,
                group_id=f"{settings.KAFKA_CONSUMER_GROUP}-ingest",
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Kafka unavailable, consumers disabled: %s", exc)
    # Best-effort: resolves the Langfuse project id so admin trace links are exact.
    await langfuse.project_id()
    yield
    for task in consumer_tasks:
        await kafka.stop_consumer(task)
    await kafka.stop_producer()
    await postgres.close_pool()
    minio.close_minio()


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=lifespan)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
