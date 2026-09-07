"""Kafka handlers for upload events — the online ingestion use-case.

One message = one uploaded file. The pipeline mirrors the offline notebooks:

    MinIO bytes -> extract text -> LLM structuring -> (JD only: semantic
    chunking) -> build points -> embed -> ensure collection -> replace the
    document's points in Qdrant

The whole run is one Langfuse trace (session = upload id). A malformed payload
re-raises so the broker dead-letters it; a pipeline failure is caught, reported
on the ``*.processed`` topic and NOT re-raised, so the offset commits and the
admin page can show the error. Point ids are deterministic and the document's
stale points are deleted before upserting, so at-least-once redelivery is safe.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Literal, Mapping

from pydantic import ValidationError

from core import chunking, embedding, extract, points, structure
from schemas.ingest import IngestProcessedEvent, JobUploadedEvent, ResumeUploadedEvent
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


async def handle_upload_event(msg: kafka.Message) -> None:
    """Dispatch by topic (the consumer subscribes to both upload topics)."""
    if msg.topic == kafka.topic_name(kafka.TOPIC_RESUME_UPLOADED):
        await handle_resume_uploaded(msg)
    else:
        await handle_job_uploaded(msg)


async def handle_resume_uploaded(msg: kafka.Message) -> None:
    try:
        event = ResumeUploadedEvent.model_validate(msg.value)
    except ValidationError as exc:
        log.error("invalid resume.uploaded payload at %s[%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
        raise
    await _run(
        kind="cv",
        topic=kafka.TOPIC_RESUME_PROCESSED,
        upload_id=event.upload_id,
        document_id=str(event.resume_id),
        event=event,
        ingest=_ingest_cv,
    )


async def handle_job_uploaded(msg: kafka.Message) -> None:
    try:
        event = JobUploadedEvent.model_validate(msg.value)
    except ValidationError as exc:
        log.error("invalid job.uploaded payload at %s[%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
        raise
    await _run(
        kind="jd",
        topic=kafka.TOPIC_JOB_PROCESSED,
        upload_id=event.upload_id,
        document_id=event.job_id,
        event=event,
        ingest=_ingest_jd,
    )


async def _run(
    *,
    kind: Literal["cv", "jd"],
    topic: str,
    upload_id: str,
    document_id: str,
    event: ResumeUploadedEvent | JobUploadedEvent,
    ingest: Callable[..., Awaitable[int]],
) -> None:
    """Shared trace + status-event wrapper around one ingestion."""
    result = IngestProcessedEvent(upload_id=upload_id, kind=kind, document_id=document_id, status="done")
    with langfuse.log_trace(
        f"ingest-{kind}",
        conversation_id=upload_id,
        input={"bucket": event.bucket, "key": event.key, "filename": event.filename},
        metadata={"document_id": document_id, "structure_model": settings.OPENAI_STRUCTURE_MODEL},
        tags=["ingestion", kind],
    ) as trace:
        result.trace_id = trace.id
        await _report(topic, result, status="processing")
        try:
            result.points = await ingest(event)
        except Exception as exc:  # noqa: BLE001 — reported to bot-agent below
            log.exception("%s ingestion failed for upload %s: %s", kind, upload_id, exc)
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            trace.update(level="ERROR", status_message=result.error)
        trace.set_io(output={"status": result.status, "points": result.points, "error": result.error})
    await _report(topic, result)
    log.info("upload %s (%s): %s (%s points)", upload_id, kind, result.status, result.points)


async def _report(topic: str, result: IngestProcessedEvent, *, status: str | None = None) -> None:
    payload = result.model_dump()
    if status is not None:
        payload.update(status=status, points=None, error=None)
    try:
        await kafka.produce(topic, payload, key=result.upload_id)
    except Exception as exc:  # noqa: BLE001 — status reporting must not kill the pipeline
        log.error("could not produce %s for upload %s: %s", topic, result.upload_id, exc)


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
def _source(event: ResumeUploadedEvent | JobUploadedEvent) -> dict:
    """Upload metadata stored on every point so the chatbot can fetch a document by file name."""
    return points.source_payload(
        filename=event.filename,
        upload_id=event.upload_id,
        bucket=event.bucket,
        key=event.key,
        content_type=event.content_type,
        uploaded_at=event.uploaded_at,
    )


async def _ingest_cv(event: ResumeUploadedEvent) -> int:
    data = await minio.get_object(event.bucket, event.key)
    with langfuse.log_span("extract", input={"filename": event.filename, "bytes": len(data)}) as span:
        text = extract.scrub_pii(extract.extract_text(data, event.filename))
        span.update(output={"chars": len(text)})

    record = await structure.structure_cv(text)
    record["id"] = event.resume_id
    drafts = points.build_cv_points(
        record, category=event.category or settings.CV_DEFAULT_CATEGORY, source=_source(event)
    )
    if not drafts:
        raise ValueError("resume produced no embeddable fields")

    with langfuse.log_span("embed", input={"points": len(drafts)}):
        vectors = await embedding.embed_drafts(drafts)

    return await _replace_points(
        settings.QDRANT_CV_COLLECTION,
        document_filter={"id": event.resume_id},
        payload_indexes=points.CV_PAYLOAD_INDEXES,
        drafts=drafts,
        vectors=vectors,
    )


async def _ingest_jd(event: JobUploadedEvent) -> int:
    data = await minio.get_object(event.bucket, event.key)
    with langfuse.log_span("extract", input={"filename": event.filename, "bytes": len(data)}) as span:
        text = extract.extract_text(data, event.filename)
        span.update(output={"chars": len(text)})

    job = await structure.structure_jd(text, job_id=event.job_id)
    with langfuse.log_span("chunk", input={"chars": len(points.job_to_text(job))}) as span:
        chunks = await chunking.semantic_chunks(points.job_to_text(job))
        span.update(output={"chunks": len(chunks)})
    drafts = points.build_jd_points(job, chunks, source=_source(event))
    if not drafts:
        raise ValueError("job posting produced no embeddable content")

    with langfuse.log_span("embed", input={"points": len(drafts)}):
        vectors = await embedding.embed_drafts(drafts)

    return await _replace_points(
        settings.QDRANT_JD_COLLECTION,
        document_filter={"id": event.job_id},
        payload_indexes=points.JD_PAYLOAD_INDEXES,
        drafts=drafts,
        vectors=vectors,
    )


async def _replace_points(
    collection: str,
    *,
    document_filter: Mapping[str, str | int],
    payload_indexes: Mapping[str, str],
    drafts: list[points.PointDraft],
    vectors: list[list[float]],
) -> int:
    """Delete the document's stale points, then upsert the fresh ones.

    Deleting first is what makes re-processing safe when the new run yields
    fewer points (e.g. a re-chunked JD) — the notebooks drop the whole JD
    collection for the same reason, which an online service must never do.
    """
    with langfuse.log_span("upsert", input={"collection": collection, "points": len(drafts)}):
        await qdrant.ensure_collection(collection, size=settings.EMBED_DIMS, payload_indexes=payload_indexes)
        await qdrant.delete_points(collection, filters=document_filter)
        return await qdrant.upsert_points(collection, [(d.id, v, d.payload) for d, v in zip(drafts, vectors, strict=True)])
