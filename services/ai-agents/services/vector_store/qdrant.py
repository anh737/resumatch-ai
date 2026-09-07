"""Qdrant adapter — vector search over the resume and job-description collections.

Collections (populated offline by ``pre-processing/``, 1536-d cosine vectors from
``text-embedding-3-small``):

* ``cv_information_technology`` — one point per *section* of a resume. Payload:
  ``id`` (int, resume id — shared by all sections of one resume), ``field``
  (keyword, the embedded section: ``information`` | ``objective`` |
  ``work_exp`` | ``technical_skills`` | ``certification``), ``category``
  (keyword), ``embedded_text`` plus the structured record (``objective``,
  ``work_exp`` …).
* ``jd_jobs`` — several points per job, all sharing ``id`` (keyword, job id).
  ``type`` tells them apart: ``chunk`` (semantic chunk of the ad; has
  ``chunk_index``) or ``field`` (one embedded section such as ``requirements`` /
  ``responsibilities``; carries the full record: ``job_title``, ``company``,
  ``location``, ``salary``, ``description``, ``requirements[]``,
  ``responsibilities[]``, ``benefits[]``, ``employment_type``). Both kinds have
  ``embedded_text``.

Because a resume/job spans several points, the domain searches
(:func:`search_resumes`, :func:`search_jobs`) use Qdrant's *group-by* query so
each result is one distinct resume/job with its best-matching sections.

Callers must embed the query text themselves (``core.llm.embed_text``) — this
module never calls OpenAI, per the layering rules in ``project_architecture.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping, Sequence

from qdrant_client import AsyncQdrantClient, models

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

# Payload keys used by the offline pipeline (see module docstring).
RESUME_ID_KEY = "id"
JOB_ID_KEY = "id"
JOB_TYPE_CHUNK = "chunk"   # semantic chunks of the ad
JOB_TYPE_FIELD = "field"   # one section each; also carry the full job record
# Upload metadata written by ai-embeddings for documents ingested through the
# admin portal (absent on the offline corpus): source = {filename, stem,
# upload_id, bucket, key, content_type, uploaded_at, ingested_at}.
SOURCE_FILENAME_KEY = "source.filename"
SOURCE_STEM_KEY = "source.stem"

# Value accepted by build_filter(): scalar -> match, list -> match any,
# {"gte"/"gt"/"lte"/"lt": n} -> range, None -> ignored.
FilterValue = str | int | float | bool | Sequence[str | int] | Mapping[str, float] | None
FilterSpec = Mapping[str, FilterValue] | models.Filter | None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Point:
    """A stored point (no similarity score)."""

    id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchHit:
    """One matching point."""

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchGroup:
    """Distinct entity (resume / job) with its best-matching points."""

    key: Any
    score: float
    hits: list[SearchHit]

    @property
    def payload(self) -> dict[str, Any]:
        """Payload of the best hit — handy for the structured record."""
        return self.hits[0].payload if self.hits else {}


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_qdrant() -> AsyncQdrantClient:
    """Process-wide async client (lazy). Safe to call from any coroutine."""
    log.debug("connecting to Qdrant at %s", settings.QDRANT_URL)
    return AsyncQdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        timeout=settings.QDRANT_TIMEOUT,
    )


async def close_qdrant() -> None:
    """Close the shared client (call from the FastAPI shutdown hook)."""
    if get_qdrant.cache_info().currsize:
        await get_qdrant().close()
        get_qdrant.cache_clear()


async def ping() -> bool:
    """True if Qdrant answers (used by /health)."""
    try:
        await get_qdrant().get_collections()
        return True
    except Exception as exc:  # noqa: BLE001 — health probes must never raise
        log.warning("Qdrant ping failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _condition(key: str, value: FilterValue) -> models.FieldCondition | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return models.FieldCondition(key=key, range=models.Range(**value))
    if isinstance(value, (list, tuple, set, frozenset)):
        return models.FieldCondition(key=key, match=models.MatchAny(any=list(value)))
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def build_filter(
    must: Mapping[str, FilterValue] | None = None,
    *,
    must_not: Mapping[str, FilterValue] | None = None,
    should: Mapping[str, FilterValue] | None = None,
) -> models.Filter | None:
    """Build a Qdrant filter from plain dicts.

    >>> build_filter({"category": "INFORMATION-TECHNOLOGY", "id": [1, 2]},
    ...              must_not={"type": "chunk"})
    Values: scalar -> exact match, list -> any of, {"gte": 3} -> range, None -> skipped.
    Returns ``None`` when every value is ``None`` so it can be passed straight through.
    """

    def conds(spec: Mapping[str, FilterValue] | None) -> list[models.FieldCondition]:
        return [c for k, v in (spec or {}).items() if (c := _condition(k, v)) is not None]

    m, mn, s = conds(must), conds(must_not), conds(should)
    if not (m or mn or s):
        return None
    return models.Filter(must=m or None, must_not=mn or None, should=s or None)


def _as_filter(spec: FilterSpec) -> models.Filter | None:
    if spec is None or isinstance(spec, models.Filter):
        return spec
    return build_filter(spec)


def has_field_filter(key: str) -> models.Filter:
    """Points whose payload has a non-empty ``key`` — e.g. only uploaded documents."""
    return models.Filter(must_not=[models.IsEmptyCondition(is_empty=models.PayloadField(key=key))])


# ---------------------------------------------------------------------------
# Generic search / read
# ---------------------------------------------------------------------------
def _hit(p: models.ScoredPoint) -> SearchHit:
    return SearchHit(id=str(p.id), score=float(p.score), payload=dict(p.payload or {}))


def _point(r: models.Record) -> Point:
    return Point(id=str(r.id), payload=dict(r.payload or {}))


async def search(
    collection: str,
    vector: Sequence[float],
    *,
    limit: int = 5,
    filters: FilterSpec = None,
    score_threshold: float | None = None,
    with_payload: bool | Sequence[str] = True,
    offset: int = 0,
) -> list[SearchHit]:
    """Nearest-neighbour search; one entry per matching *point*."""
    resp = await get_qdrant().query_points(
        collection_name=collection,
        query=list(vector),
        query_filter=_as_filter(filters),
        limit=limit,
        offset=offset,
        score_threshold=score_threshold,
        with_payload=with_payload,
        with_vectors=False,
    )
    hits = [_hit(p) for p in resp.points]
    log.debug("search %s: %d hits (limit=%d)", collection, len(hits), limit)
    return hits


async def search_grouped(
    collection: str,
    vector: Sequence[float],
    *,
    group_by: str,
    limit: int = 5,
    group_size: int = 3,
    filters: FilterSpec = None,
    score_threshold: float | None = None,
    with_payload: bool | Sequence[str] = True,
) -> list[SearchGroup]:
    """Nearest-neighbour search collapsed to ``limit`` distinct ``group_by`` values.

    Each group carries up to ``group_size`` of its best points, ordered by score.
    """
    resp = await get_qdrant().query_points_groups(
        collection_name=collection,
        query=list(vector),
        group_by=group_by,
        limit=limit,
        group_size=group_size,
        query_filter=_as_filter(filters),
        score_threshold=score_threshold,
        with_payload=with_payload,
        with_vectors=False,
    )
    groups = [
        SearchGroup(key=g.id, score=float(g.hits[0].score) if g.hits else 0.0, hits=[_hit(h) for h in g.hits])
        for g in resp.groups
    ]
    log.debug("search_grouped %s by %s: %d groups", collection, group_by, len(groups))
    return groups


async def scroll(
    collection: str,
    *,
    filters: FilterSpec = None,
    limit: int = 100,
    with_payload: bool | Sequence[str] = True,
    max_points: int | None = None,
) -> list[Point]:
    """Read points matching ``filters`` (no vector), following pagination up to ``max_points``."""
    client = get_qdrant()
    out: list[Point] = []
    next_offset: Any = None
    while True:
        page_size = limit if max_points is None else min(limit, max_points - len(out))
        if page_size <= 0:
            break
        records, next_offset = await client.scroll(
            collection_name=collection,
            scroll_filter=_as_filter(filters),
            limit=page_size,
            offset=next_offset,
            with_payload=with_payload,
            with_vectors=False,
        )
        out.extend(_point(r) for r in records)
        if next_offset is None or not records:
            break
    return out


async def retrieve(collection: str, ids: Sequence[str | int], *, with_payload: bool | Sequence[str] = True) -> list[Point]:
    """Fetch points by Qdrant point id."""
    if not ids:
        return []
    records = await get_qdrant().retrieve(collection_name=collection, ids=list(ids), with_payload=with_payload, with_vectors=False)
    return [_point(r) for r in records]


async def count(collection: str, *, filters: FilterSpec = None, exact: bool = True) -> int:
    """Number of points matching ``filters``."""
    res = await get_qdrant().count(collection_name=collection, count_filter=_as_filter(filters), exact=exact)
    return int(res.count)


async def list_collections() -> list[str]:
    res = await get_qdrant().get_collections()
    return [c.name for c in res.collections]


async def collection_exists(collection: str) -> bool:
    return bool(await get_qdrant().collection_exists(collection))


# ---------------------------------------------------------------------------
# Domain searches (resume-scan)
# ---------------------------------------------------------------------------
async def search_resumes(
    vector: Sequence[float],
    *,
    limit: int = 5,
    group_size: int = 3,
    category: str | Sequence[str] | None = None,
    section: str | Sequence[str] | None = None,
    resume_ids: Sequence[int] | None = None,
    filename: str | None = None,
    score_threshold: float | None = None,
    extra_filters: Mapping[str, FilterValue] | None = None,
) -> list[SearchGroup]:
    """Top ``limit`` distinct resumes for a query vector.

    ``group.key`` is the resume id, ``group.hits`` its best-matching sections
    (``payload["field"]`` says which). ``section`` restricts which resume
    sections are searched (payload ``field``), ``category`` the resume category,
    ``filename`` the exact upload file name (``source.filename``).
    """
    filters = {
        "category": category,
        "field": section,
        RESUME_ID_KEY: list(resume_ids) if resume_ids else None,
        SOURCE_FILENAME_KEY: filename,
    }
    if extra_filters:
        filters.update(extra_filters)
    return await search_grouped(
        settings.QDRANT_CV_COLLECTION,
        vector,
        group_by=RESUME_ID_KEY,
        limit=limit,
        group_size=group_size,
        filters=filters,
        score_threshold=score_threshold,
    )


async def search_jobs(
    vector: Sequence[float],
    *,
    limit: int = 5,
    group_size: int = 3,
    company: str | Sequence[str] | None = None,
    employment_type: str | Sequence[str] | None = None,
    point_type: str | Sequence[str] | None = JOB_TYPE_CHUNK,
    job_ids: Sequence[str] | None = None,
    filename: str | None = None,
    score_threshold: float | None = None,
    extra_filters: Mapping[str, FilterValue] | None = None,
) -> list[SearchGroup]:
    """Top ``limit`` distinct jobs for a query vector.

    ``group.key`` is the job id, ``group.hits`` its best-matching points.
    ``point_type`` selects the point kind: :data:`JOB_TYPE_CHUNK` (default),
    :data:`JOB_TYPE_FIELD` (full record in the payload) or ``None`` for both.
    ``filename`` restricts to the job uploaded with that exact file name.
    """
    filters = {
        "company": company,
        "employment_type": employment_type,
        "type": point_type,
        JOB_ID_KEY: list(job_ids) if job_ids else None,
        SOURCE_FILENAME_KEY: filename,
    }
    if extra_filters:
        filters.update(extra_filters)
    return await search_grouped(
        settings.QDRANT_JD_COLLECTION,
        vector,
        group_by=JOB_ID_KEY,
        limit=limit,
        group_size=group_size,
        filters=filters,
        score_threshold=score_threshold,
    )


async def get_resume(resume_id: int) -> list[Point]:
    """All stored sections of one resume (empty list if unknown)."""
    return await scroll(settings.QDRANT_CV_COLLECTION, filters={RESUME_ID_KEY: resume_id}, limit=50)


async def get_job(job_id: str) -> list[Point]:
    """All stored points of one job, chunks in order (empty list if unknown)."""
    points = await scroll(settings.QDRANT_JD_COLLECTION, filters={JOB_ID_KEY: job_id}, limit=100)
    return sorted(points, key=lambda p: (p.payload.get("type") != JOB_TYPE_CHUNK, p.payload.get("chunk_index", 0)))


# ---------------------------------------------------------------------------
# Uploaded documents (points carrying a `source` block)
# ---------------------------------------------------------------------------
async def find_resumes_by_source(*, filename: str | None = None, stem: str | None = None, limit: int = 50) -> list[Point]:
    """Sections of the resume uploaded as ``filename`` (exact) or with lookup key ``stem``."""
    if not filename and not stem:
        return []
    return await scroll(settings.QDRANT_CV_COLLECTION, filters={SOURCE_FILENAME_KEY: filename, SOURCE_STEM_KEY: stem}, limit=limit)


async def find_jobs_by_source(*, filename: str | None = None, stem: str | None = None, limit: int = 100) -> list[Point]:
    """Points of the job posting uploaded as ``filename`` (exact) or with lookup key ``stem``."""
    if not filename and not stem:
        return []
    return await scroll(settings.QDRANT_JD_COLLECTION, filters={SOURCE_FILENAME_KEY: filename, SOURCE_STEM_KEY: stem}, limit=limit)


async def list_uploaded_resumes(*, max_points: int = 2000) -> list[Point]:
    """Every point of every resume that was uploaded through the admin portal (payload: id, category, source)."""
    return await scroll(
        settings.QDRANT_CV_COLLECTION,
        filters=has_field_filter(SOURCE_FILENAME_KEY),
        limit=200,
        with_payload=["id", "category", "source"],
        max_points=max_points,
    )


async def list_uploaded_jobs(*, max_points: int = 4000) -> list[Point]:
    """Every point of every job posting uploaded through the admin portal (payload: id, job_title, company, source)."""
    return await scroll(
        settings.QDRANT_JD_COLLECTION,
        filters=has_field_filter(SOURCE_FILENAME_KEY),
        limit=200,
        with_payload=["id", "job_title", "company", "source"],
        max_points=max_points,
    )
