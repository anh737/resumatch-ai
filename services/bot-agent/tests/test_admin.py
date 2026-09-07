"""handler.admin — uploads, ingestion status and the Langfuse proxy with fakes."""

from datetime import datetime, timezone
from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers, UploadFile

import handler.admin as admin
from schemas.admin import trace_detail_to_out
from services.database import postgres
from services.message_broker import kafka
from services.object_store import minio
from services.observability import langfuse
from setting import settings

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _file(name: str, data: bytes = b"%PDF-1.4 hello", content_type: str = "application/pdf") -> UploadFile:
    return UploadFile(BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


@pytest.fixture
def store(monkeypatch):
    """Fake MinIO + Postgres + Kafka; records every call."""
    calls: dict[str, list] = {"minio": [], "rows": [], "kafka": [], "updates": []}

    async def ensure_bucket(bucket):
        calls["minio"].append(("ensure", bucket))

    async def put_object(bucket, key, data, *, content_type):
        calls["minio"].append(("put", bucket, key, len(data), content_type))

    async def insert_upload(upload_id, *, kind, document_id, filename, bucket, object_key, category=None):
        rec = postgres.UploadRecord(
            id=upload_id, kind=kind, document_id=document_id, filename=filename, bucket=bucket,
            object_key=object_key, status="queued", points=None, error=None, trace_id=None,
            created_at=NOW, updated_at=NOW, category=category,
        )
        calls["rows"].append(rec)
        return rec

    async def get_upload(upload_id):
        return next((r for r in calls["rows"] if r.id == upload_id), None)

    async def reset_upload(upload_id):
        rec = await get_upload(upload_id)
        if rec:
            rec.status, rec.points, rec.error, rec.trace_id = "queued", None, None, None
            calls["updates"].append((upload_id, "reset"))
        return rec

    async def update_upload_status(upload_id, *, status, points=None, error=None, trace_id=None):
        calls["updates"].append((upload_id, status, points, error, trace_id))
        return upload_id != "unknown"

    async def produce(topic, value, *, key=None, **_):
        calls["kafka"].append((topic, value, key))
        return kafka.Delivery(topic=topic, partition=0, offset=0, timestamp_ms=0)

    monkeypatch.setattr(minio, "ensure_bucket", ensure_bucket)
    monkeypatch.setattr(minio, "put_object", put_object)
    monkeypatch.setattr(postgres, "insert_upload", insert_upload)
    monkeypatch.setattr(postgres, "get_upload", get_upload)
    monkeypatch.setattr(postgres, "reset_upload", reset_upload)
    monkeypatch.setattr(postgres, "update_upload_status", update_upload_status)
    monkeypatch.setattr(kafka, "produce", produce)
    return calls


async def test_cv_upload_stores_file_then_produces_keyed_event(store):
    out = await admin.submit_upload("cv", _file("Jane Doe (2026).pdf"), category="  ")

    assert out.kind == "cv" and out.status == "queued" and out.filename == "Jane Doe _2026_.pdf"
    assert out.document_id.isdigit()  # CV ids are integers (epoch ms)
    (ensure, put) = store["minio"]
    assert ensure == ("ensure", settings.MINIO_RESUME_BUCKET)
    assert put[1:] == (settings.MINIO_RESUME_BUCKET, f"{out.document_id}/{out.filename}", 14, "application/pdf")

    (topic, value, key), = store["kafka"]
    assert topic == kafka.TOPIC_RESUME_UPLOADED and key == out.id
    assert value == {
        "upload_id": out.id,
        "resume_id": int(out.document_id),
        "bucket": settings.MINIO_RESUME_BUCKET,
        "key": f"{out.document_id}/{out.filename}",
        "filename": out.filename,
        "content_type": "application/pdf",
        "category": None,  # blank category -> ai-embeddings default
        "uploaded_at": NOW.isoformat(),
    }


async def test_jd_upload_uses_job_bucket_and_string_id(store):
    out = await admin.submit_upload("jd", _file("role.md", b"# Data Engineer", "text/markdown"))

    assert out.document_id.startswith("upload_")
    (topic, value, key), = store["kafka"]
    assert topic == kafka.TOPIC_JOB_UPLOADED and key == out.id
    assert value["job_id"] == out.document_id and value["bucket"] == settings.MINIO_JOB_BUCKET
    assert value["uploaded_at"] == NOW.isoformat()
    assert "resume_id" not in value and "category" not in value


async def test_reprocess_requeues_existing_file_without_reupload(store):
    first = await admin.submit_upload("cv", _file("cv.pdf"), category="DATA-SCIENCE")
    assert first.category == "DATA-SCIENCE"
    store["rows"][0].status = "failed"

    again = await admin.reprocess_upload(first.id)
    assert again.status == "queued" and again.id == first.id and again.document_id == first.document_id
    assert len(store["minio"]) == 2  # ensure + one put: no second upload to MinIO
    assert [t for t, *_ in store["kafka"]] == [kafka.TOPIC_RESUME_UPLOADED] * 2
    replay = store["kafka"][1][1]
    assert replay["key"] == f"{first.document_id}/cv.pdf" and replay["category"] == "DATA-SCIENCE"
    assert replay["content_type"] == "application/pdf" and replay["uploaded_at"] == NOW.isoformat()

    with pytest.raises(HTTPException) as exc:
        await admin.reprocess_upload("missing")
    assert exc.value.status_code == 404
    store["rows"][0].status = "processing"
    with pytest.raises(HTTPException) as exc:
        await admin.reprocess_upload(first.id)
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "file, status",
    [
        (_file("photo.png", b"x", "image/png"), 415),
        (_file("empty.pdf", b""), 400),
        (_file("huge.pdf", b"x" * (settings.UPLOAD_MAX_MB * 1024 * 1024 + 1)), 413),
    ],
)
async def test_invalid_uploads_are_rejected_before_any_io(store, file, status):
    with pytest.raises(HTTPException) as exc:
        await admin.submit_upload("cv", file)
    assert exc.value.status_code == status
    assert store["minio"] == [] and store["kafka"] == []


async def test_processed_event_updates_row(store):
    msg = kafka.Message(
        topic="resume-scan.resume.processed", key="u1",
        value={"upload_id": "u1", "kind": "cv", "document_id": "1", "status": "done", "points": 5, "trace_id": "abc"},
    )
    await admin.handle_ingest_processed(msg)
    assert store["updates"] == [("u1", "done", 5, None, "abc")]

    await admin.handle_ingest_processed(kafka.Message(topic="t", key="unknown", value={**msg.value, "upload_id": "unknown"}))
    assert len(store["updates"]) == 2  # unknown id is logged and ignored, not raised

    with pytest.raises(ValidationError):
        await admin.handle_ingest_processed(kafka.Message(topic="t", key=None, value={"status": "done"}))


async def test_stats_aggregate_per_kind(monkeypatch):
    async def upload_stats():
        return [
            postgres.UploadStatsRow(kind="cv", status="done", count=3, points=15),
            postgres.UploadStatsRow(kind="cv", status="failed", count=1, points=0),
            postgres.UploadStatsRow(kind="jd", status="processing", count=2, points=0),
        ], NOW

    monkeypatch.setattr(postgres, "upload_stats", upload_stats)
    stats = await admin.get_upload_stats()
    assert (stats.all.total, stats.all.done, stats.all.failed, stats.all.processing, stats.all.points) == (6, 3, 1, 2, 15)
    assert (stats.cv.total, stats.cv.points) == (4, 15)
    assert (stats.jd.total, stats.jd.processing) == (2, 2)
    assert stats.last_upload_at == NOW


# ---------------------------------------------------------------------------
# Langfuse proxy
# ---------------------------------------------------------------------------
@pytest.fixture
def langfuse_keys(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_URL", "http://localhost:3031/")
    monkeypatch.setattr(langfuse, "_project_id", None)


async def test_traces_report_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    out = await admin.list_traces()
    assert out.configured is False and out.items == [] and out.error is None


async def test_traces_degrade_when_langfuse_is_down(monkeypatch, langfuse_keys):
    async def project_id():
        return None

    async def list_traces(**_):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(langfuse, "project_id", project_id)
    monkeypatch.setattr(langfuse, "list_traces", list_traces)
    out = await admin.list_traces(tags=["ingestion"])
    assert out.configured is True and out.error == "ConnectError: boom" and out.items == []


async def test_traces_are_mapped_with_deep_links(monkeypatch, langfuse_keys):
    seen = {}

    async def project_id():
        langfuse._project_id = "proj1"
        return "proj1"

    async def list_traces(*, limit, page, tags, name):
        seen.update(limit=limit, page=page, tags=list(tags or []), name=name)
        return [
            {"id": "t1", "name": "ingest-cv", "tags": ["ingestion", "cv"], "latency": "1.5", "totalCost": 0.002,
             "sessionId": "u1", "htmlPath": "/project/proj1/traces/t1"},
            {"id": "t2", "name": "chat", "tags": None, "latency": None},
            {"name": "no id -> skipped"},
        ], {"totalItems": 42, "totalPages": 3}

    monkeypatch.setattr(langfuse, "project_id", project_id)
    monkeypatch.setattr(langfuse, "list_traces", list_traces)

    out = await admin.list_traces(10, page=2, tags=["ingestion"], name="ingest-cv")
    assert seen == {"limit": 10, "page": 2, "tags": ["ingestion"], "name": "ingest-cv"}
    assert (out.total, out.total_pages, out.page) == (42, 3, 2)
    assert [t.id for t in out.items] == ["t1", "t2"]
    assert out.items[0].url == "http://localhost:3031/project/proj1/traces/t1"  # htmlPath wins
    assert out.items[0].latency_seconds == 1.5 and out.items[0].tags == ["ingestion", "cv"]
    assert out.items[1].url == "http://localhost:3031/project/proj1/traces/t2"  # cached project id
    assert out.items[1].tags == [] and out.items[1].latency_seconds is None


def test_trace_url_fallbacks(langfuse_keys):
    assert langfuse.trace_url("t9") == "http://localhost:3031/trace/t9"
    langfuse._project_id = "p"
    assert langfuse.trace_url("t9") == "http://localhost:3031/project/p/traces/t9"
    assert langfuse.trace_url("t9", html_path="project/p/traces/t9") == "http://localhost:3031/project/p/traces/t9"


async def test_trace_detail_builds_observation_tree(monkeypatch, langfuse_keys):
    async def project_id():
        return None

    async def get_trace(trace_id):
        assert trace_id == "t1"
        return {
            "id": "t1", "name": "ingest-jd", "tags": ["ingestion", "jd"], "latency": 4.2, "totalCost": "0.01",
            "metadata": {"document_id": "upload_1"}, "input": {"key": "upload_1/jd.pdf"}, "output": {"status": "done"},
            "scores": [{"name": "user-feedback", "value": 1, "comment": None}, {"value": 2}],
            "observations": [
                {"id": "o2", "parentObservationId": "o1", "type": "GENERATION", "name": "jd_extraction",
                 "startTime": "2026-09-06T12:00:01Z", "model": "gpt-4.1",
                 "usageDetails": {"input": 100, "output": 20, "total": 120}, "calculatedTotalCost": 0.004},
                {"id": "o1", "parentObservationId": None, "type": "SPAN", "name": "extract",
                 "startTime": "2026-09-06T12:00:00Z", "endTime": "2026-09-06T12:00:00.500Z", "latency": 0.5,
                 "level": "ERROR", "statusMessage": "ValueError: empty"},
                "garbage",
            ],
        }

    monkeypatch.setattr(langfuse, "project_id", project_id)
    monkeypatch.setattr(langfuse, "get_trace", get_trace)

    detail = await admin.get_trace("t1")
    assert detail.url == "http://localhost:3031/trace/t1"
    assert detail.total_cost == 0.01 and detail.metadata == {"document_id": "upload_1"}
    assert [o.id for o in detail.observations] == ["o1", "o2"]  # sorted by start time
    gen = detail.observations[1]
    assert (gen.parent_id, gen.type, gen.model, gen.total_tokens, gen.total_cost) == ("o1", "GENERATION", "gpt-4.1", 120, 0.004)
    assert detail.observations[0].level == "ERROR" and detail.observations[0].latency_seconds == 0.5
    assert [s.name for s in detail.scores] == ["user-feedback"]


async def test_trace_detail_errors(monkeypatch, langfuse_keys):
    async def project_id():
        return None

    async def missing(trace_id):
        return None

    monkeypatch.setattr(langfuse, "project_id", project_id)
    monkeypatch.setattr(langfuse, "get_trace", missing)
    with pytest.raises(HTTPException) as exc:
        await admin.get_trace("nope")
    assert exc.value.status_code == 404

    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    with pytest.raises(HTTPException) as exc:
        await admin.get_trace("nope")
    assert exc.value.status_code == 503


def test_detail_mapping_tolerates_missing_fields():
    out = trace_detail_to_out({"id": "x"}, "http://u")
    assert out.observations == [] and out.scores == [] and out.tags == [] and out.metadata is None
