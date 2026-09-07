"""Admin contracts: the upload / observability API of the admin portal and the
ingestion Kafka payloads.

The Kafka models mirror ``ai-embeddings/schemas/ingest.py`` — the two services
share the wire format of ``resume.uploaded`` / ``job.uploaded`` and
``resume.processed`` / ``job.processed``.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

UploadKind = Literal["cv", "jd"]
UploadStatus = Literal["queued", "processing", "done", "failed"]


# ---------------------------------------------------------------------------
# HTTP API (admin-portal -> bot-agent): uploads
# ---------------------------------------------------------------------------
class UploadOut(BaseModel):
    """One ingestion_uploads row, as shown in the admin tables."""

    id: str
    kind: UploadKind
    document_id: str
    filename: str
    category: str | None = None   # CV category requested at upload time (payload `category`)
    status: UploadStatus
    points: int | None = None     # Qdrant points upserted (done only)
    error: str | None = None      # "ExcType: message" (failed only)
    trace_id: str | None = None   # Langfuse trace of the ingestion
    trace_url: str | None = None  # deep link into the Langfuse UI
    created_at: datetime
    updated_at: datetime


class UploadCounts(BaseModel):
    """Upload counters for one kind (or for everything)."""

    total: int = 0
    queued: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
    points: int = 0  # Qdrant points upserted by the `done` uploads


class UploadStatsOut(BaseModel):
    """GET /admin/uploads/stats — the overview page's counters."""

    all: UploadCounts = Field(default_factory=UploadCounts)
    cv: UploadCounts = Field(default_factory=UploadCounts)
    jd: UploadCounts = Field(default_factory=UploadCounts)
    last_upload_at: datetime | None = None


# ---------------------------------------------------------------------------
# HTTP API (admin-portal -> bot-agent): Langfuse observability
# ---------------------------------------------------------------------------
class TraceOut(BaseModel):
    """One Langfuse trace summary for the observability page."""

    id: str
    name: str | None = None
    timestamp: datetime | None = None
    session_id: str | None = None
    user_id: str | None = None
    environment: str | None = None
    latency_seconds: float | None = None
    total_cost: float | None = None
    tags: list[str] = []
    url: str  # deep link into the Langfuse UI


class TracesOut(BaseModel):
    """GET /admin/traces — degrades gracefully when Langfuse is not configured."""

    configured: bool
    public_url: str            # browser-facing Langfuse base URL
    items: list[TraceOut] = []
    page: int = 1
    limit: int = 20
    total: int | None = None        # total traces matching the filter (from the API's meta)
    total_pages: int | None = None
    error: str | None = None   # set when Langfuse was configured but unreachable


class ObservationOut(BaseModel):
    """One observation (span / generation / tool ...) of a trace."""

    id: str
    parent_id: str | None = None
    type: str
    name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    latency_seconds: float | None = None
    model: str | None = None
    level: str | None = None
    status_message: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    input: Any = None
    output: Any = None


class ScoreOut(BaseModel):
    name: str
    value: float | str | None = None
    comment: str | None = None


class TraceDetailOut(BaseModel):
    """GET /admin/traces/{trace_id} — one trace with its observation tree."""

    id: str
    name: str | None = None
    timestamp: datetime | None = None
    session_id: str | None = None
    user_id: str | None = None
    environment: str | None = None
    release: str | None = None
    latency_seconds: float | None = None
    total_cost: float | None = None
    tags: list[str] = []
    metadata: dict[str, Any] | None = None
    input: Any = None
    output: Any = None
    url: str
    observations: list[ObservationOut] = []
    scores: list[ScoreOut] = []


# ---------------------------------------------------------------------------
# Kafka payloads (mirror ai-embeddings/schemas/ingest.py)
# ---------------------------------------------------------------------------
class ResumeUploadedEvent(BaseModel):
    upload_id: str
    resume_id: int             # payload "id" in cv_information_technology (integer!)
    bucket: str
    key: str
    filename: str
    content_type: str | None = None
    category: str | None = None
    uploaded_at: str | None = None  # ISO timestamp of the upload row; stored as payload source.uploaded_at


class JobUploadedEvent(BaseModel):
    upload_id: str
    job_id: str                # payload "id" in jd_jobs (string)
    bucket: str
    key: str
    filename: str
    content_type: str | None = None
    uploaded_at: str | None = None


class IngestProcessedEvent(BaseModel):
    upload_id: str
    kind: UploadKind
    document_id: str
    status: Literal["processing", "done", "failed"]
    points: int | None = None
    error: str | None = None
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Mapping helpers (raw Langfuse API dicts -> response models), defensive on purpose
# ---------------------------------------------------------------------------
def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _tags(value: Any) -> list[str]:
    return [str(t) for t in value] if isinstance(value, list) else []


def trace_dict_to_out(trace: dict[str, Any], url: str) -> TraceOut:
    """Map a raw ``/api/public/traces`` item onto :class:`TraceOut`."""
    return TraceOut(
        id=str(trace.get("id")),
        name=trace.get("name"),
        timestamp=trace.get("timestamp"),
        session_id=trace.get("sessionId"),
        user_id=trace.get("userId"),
        environment=trace.get("environment"),
        latency_seconds=_float(trace.get("latency")),
        total_cost=_float(trace.get("totalCost")),
        tags=_tags(trace.get("tags")),
        url=url,
    )


def observation_dict_to_out(obs: dict[str, Any]) -> ObservationOut:
    """Map one ``observations[]`` entry of ``/api/public/traces/{id}``."""
    usage = obs.get("usageDetails") if isinstance(obs.get("usageDetails"), dict) else {}
    legacy = obs.get("usage") if isinstance(obs.get("usage"), dict) else {}
    cost = obs.get("costDetails") if isinstance(obs.get("costDetails"), dict) else {}
    return ObservationOut(
        id=str(obs.get("id")),
        parent_id=obs.get("parentObservationId"),
        type=str(obs.get("type") or "SPAN"),
        name=obs.get("name"),
        start_time=obs.get("startTime"),
        end_time=obs.get("endTime"),
        latency_seconds=_float(obs.get("latency")),
        model=obs.get("model"),
        level=obs.get("level"),
        status_message=obs.get("statusMessage"),
        input_tokens=_int(usage.get("input", legacy.get("input"))),
        output_tokens=_int(usage.get("output", legacy.get("output"))),
        total_tokens=_int(usage.get("total", legacy.get("total"))),
        total_cost=_float(obs.get("calculatedTotalCost", obs.get("totalPrice", cost.get("total")))),
        input=obs.get("input"),
        output=obs.get("output"),
    )


def trace_detail_to_out(trace: dict[str, Any], url: str) -> TraceDetailOut:
    """Map ``/api/public/traces/{id}`` onto :class:`TraceDetailOut` (observations sorted by start time)."""
    raw_obs = [o for o in (trace.get("observations") or []) if isinstance(o, dict)]
    observations = sorted(
        (observation_dict_to_out(o) for o in raw_obs),
        key=lambda o: (o.start_time is None, o.start_time.isoformat() if o.start_time else ""),
    )
    scores = [
        ScoreOut(name=str(s.get("name")), value=s.get("value"), comment=s.get("comment"))
        for s in (trace.get("scores") or [])
        if isinstance(s, dict) and s.get("name")
    ]
    metadata = trace.get("metadata")
    return TraceDetailOut(
        id=str(trace.get("id")),
        name=trace.get("name"),
        timestamp=trace.get("timestamp"),
        session_id=trace.get("sessionId"),
        user_id=trace.get("userId"),
        environment=trace.get("environment"),
        release=trace.get("release"),
        latency_seconds=_float(trace.get("latency")),
        total_cost=_float(trace.get("totalCost")),
        tags=_tags(trace.get("tags")),
        metadata=metadata if isinstance(metadata, dict) else None,
        input=trace.get("input"),
        output=trace.get("output"),
        url=url,
        observations=observations,
        scores=scores,
    )
