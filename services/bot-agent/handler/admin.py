"""Admin use-cases: file ingestion and the Langfuse observability pages of the
admin portal.

Upload path (HTTP): validate the file, store the raw bytes in MinIO
(``<bucket>/<document_id>/<filename>``), record a ``queued`` row in Postgres
and produce ``resume.uploaded`` / ``job.uploaded`` keyed by the upload id —
ai-embeddings takes over from there (MinIO -> extract -> structure -> embed ->
Qdrant).

Status path (Kafka): ``resume.processed`` / ``job.processed`` events move the
row through ``processing`` to ``done``/``failed``; the admin portal polls
``GET /admin/uploads``. ``POST /admin/uploads/{id}/reprocess`` re-produces the
upload event for a file that is already in MinIO (after a prompt change, say).

Observability: ``GET /admin/traces`` and ``GET /admin/traces/{id}`` proxy the
Langfuse public API (read-only) so the portal can list traces and open an
observation tree without the keys ever reaching the browser.

Document ids: CVs get an integer id (epoch ms — the CV collection's payload
``id`` is an integer, like the offline corpus ids); JDs get ``upload_<epoch
ms>``, following the offline ``<source>_<id>`` convention.
"""

from __future__ import annotations

import mimetypes
import re
import time
import uuid
from pathlib import PurePosixPath
from typing import Sequence

from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from schemas.admin import (
    IngestProcessedEvent,
    JobUploadedEvent,
    ResumeUploadedEvent,
    TraceDetailOut,
    TracesOut,
    UploadCounts,
    UploadKind,
    UploadOut,
    UploadStatsOut,
    UploadStatus,
    trace_detail_to_out,
    trace_dict_to_out,
)
from services.database import postgres
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}  # must match ai-embeddings core.extract


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]", "_", name).strip() or "upload"
    return name[:120]


def _upload_out(record: postgres.UploadRecord) -> UploadOut:
    return UploadOut(
        id=record.id,
        kind=record.kind,
        document_id=record.document_id,
        filename=record.filename,
        category=record.category,
        status=record.status,
        points=record.points,
        error=record.error,
        trace_id=record.trace_id,
        trace_url=langfuse.trace_url(record.trace_id) if record.trace_id else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
async def submit_upload(kind: UploadKind, file: UploadFile, *, category: str | None = None) -> UploadOut:
    filename = _safe_filename(file.filename or "upload")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, f"unsupported file type {extension or filename!r}; use one of {sorted(SUPPORTED_EXTENSIONS)}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "the uploaded file is empty")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"file exceeds the {settings.UPLOAD_MAX_MB} MB limit")

    upload_id = str(uuid.uuid4())
    epoch_ms = time.time_ns() // 1_000_000
    if kind == "cv":
        document_id = str(epoch_ms)
        bucket = settings.MINIO_RESUME_BUCKET
    else:
        document_id = f"upload_{epoch_ms}"
        bucket = settings.MINIO_JOB_BUCKET
    object_key = f"{document_id}/{filename}"
    content_type = file.content_type or "application/octet-stream"

    await minio.ensure_bucket(bucket)
    await minio.put_object(bucket, object_key, data, content_type=content_type)
    record = await postgres.insert_upload(
        upload_id,
        kind=kind,
        document_id=document_id,
        filename=filename,
        bucket=bucket,
        object_key=object_key,
        category=(category or "").strip() or None,
    )
    await _publish_upload_event(record, content_type=content_type)
    log.info("queued %s upload: id=%s document=%s key=%s/%s (%d bytes)", kind, upload_id, document_id, bucket, object_key, len(data))
    return _upload_out(record)


def _guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


async def _publish_upload_event(record: postgres.UploadRecord, *, content_type: str | None = None) -> None:
    """Produce ``resume.uploaded`` / ``job.uploaded`` for an upload row (initial upload and re-process)."""
    common = dict(
        upload_id=record.id,
        bucket=record.bucket,
        key=record.object_key,
        filename=record.filename,
        content_type=content_type or _guess_content_type(record.filename),
        uploaded_at=record.created_at.isoformat(),
    )
    if record.kind == "cv":
        event: ResumeUploadedEvent | JobUploadedEvent = ResumeUploadedEvent(
            resume_id=int(record.document_id), category=record.category, **common
        )
        topic = kafka.TOPIC_RESUME_UPLOADED
    else:
        event = JobUploadedEvent(job_id=record.document_id, **common)
        topic = kafka.TOPIC_JOB_UPLOADED
    # Key = upload id: ai-embeddings reads the MinIO object named in the event
    # and answers on *.processed with the same key, so one upload stays ordered.
    await kafka.produce(topic, event.model_dump(mode="json"), key=record.id)


async def reprocess_upload(upload_id: str) -> UploadOut:
    """Run the ingestion again for a file that is already in MinIO (e.g. after a
    prompt or pipeline change). ai-embeddings replaces the document's points."""
    record = await postgres.get_upload(upload_id)
    if record is None:
        raise HTTPException(404, "upload not found")
    if record.status in ("queued", "processing"):
        raise HTTPException(409, f"upload is already {record.status}")
    record = await postgres.reset_upload(upload_id) or record
    await _publish_upload_event(record)
    log.info("re-queued %s upload: id=%s document=%s key=%s/%s", record.kind, record.id, record.document_id, record.bucket, record.object_key)
    return _upload_out(record)


async def list_uploads(limit: int = 50, *, kind: UploadKind | None = None, status: UploadStatus | None = None) -> list[UploadOut]:
    return [_upload_out(r) for r in await postgres.list_uploads(limit, kind=kind, status=status)]


async def get_upload(upload_id: str) -> UploadOut:
    record = await postgres.get_upload(upload_id)
    if record is None:
        raise HTTPException(404, "upload not found")
    return _upload_out(record)


async def get_upload_stats() -> UploadStatsOut:
    rows, last_upload_at = await postgres.upload_stats()
    out = UploadStatsOut(last_upload_at=last_upload_at)
    for row in rows:
        for bucket in (out.all, getattr(out, row.kind, None)):
            if bucket is None:
                continue
            bucket.total += row.count
            setattr(bucket, row.status, getattr(bucket, row.status, 0) + row.count)
            if row.status == "done":
                bucket.points += row.points
    return out


async def handle_ingest_processed(msg: kafka.Message) -> None:
    """Kafka handler for ``resume.processed`` / ``job.processed`` (registered in ``main.py``)."""
    try:
        event = IngestProcessedEvent.model_validate(msg.value)
    except ValidationError as exc:
        log.error("invalid %s payload at [%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
        raise

    updated = await postgres.update_upload_status(
        event.upload_id,
        status=event.status,
        points=event.points,
        error=event.error,
        trace_id=event.trace_id,
    )
    if not updated:
        log.info("ignored %s for upload %s (unknown id or stale status)", event.status, event.upload_id)
        return
    log.info("upload %s -> %s (points=%s, error=%s)", event.upload_id, event.status, event.points, event.error)


# ---------------------------------------------------------------------------
# Observability (Langfuse)
# ---------------------------------------------------------------------------
async def list_traces(
    limit: int = 20,
    *,
    page: int = 1,
    tags: Sequence[str] | None = None,
    name: str | None = None,
) -> TracesOut:
    """Recent Langfuse traces for the observability page; degrades instead of failing."""
    out = TracesOut(configured=langfuse.enabled(), public_url=langfuse.public_url(), page=page, limit=limit)
    if not out.configured:
        return out
    await langfuse.project_id()  # best-effort, makes trace links exact
    try:
        raw, meta = await langfuse.list_traces(limit=limit, page=page, tags=tags, name=name)
    except Exception as exc:  # noqa: BLE001 — the page shows the error, the portal keeps working
        log.warning("could not fetch Langfuse traces: %s", exc)
        out.error = f"{type(exc).__name__}: {exc}"
        return out
    out.items = [
        trace_dict_to_out(t, langfuse.trace_url(str(t.get("id")), html_path=t.get("htmlPath")))
        for t in raw
        if t.get("id")
    ]
    out.total = meta.get("totalItems")
    out.total_pages = meta.get("totalPages")
    return out


async def get_trace(trace_id: str) -> TraceDetailOut:
    """One trace with its observation tree (``503`` when Langfuse is not configured)."""
    if not langfuse.enabled():
        raise HTTPException(503, "Langfuse is not configured on bot-agent (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)")
    await langfuse.project_id()
    try:
        raw = await langfuse.get_trace(trace_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not fetch Langfuse trace %s: %s", trace_id, exc)
        raise HTTPException(502, f"Langfuse request failed: {type(exc).__name__}: {exc}") from exc
    if raw is None:
        raise HTTPException(404, "trace not found")
    return trace_detail_to_out(raw, langfuse.trace_url(trace_id, html_path=raw.get("htmlPath")))
