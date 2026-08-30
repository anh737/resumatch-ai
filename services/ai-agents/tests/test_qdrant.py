"""services.vector_store.qdrant — filter builder (pure) + live search against the local Qdrant.

Live tests skip when Qdrant is unreachable or the pre-processing output is
missing. They use a vector straight from ``points.jsonl`` as the query, so no
OpenAI call is needed and the top hit must be that very point (score ≈ 1).
"""

import json
from pathlib import Path

import httpx
import pytest
from qdrant_client import models

from services.vector_store import qdrant
from setting import settings

REPO = Path(__file__).resolve().parents[3]
CV_POINTS = REPO / "pre-processing" / "output" / "points.jsonl"
JD_POINTS = REPO / "pre-processing" / "output" / "jd_points.jsonl"


def _reachable() -> bool:
    try:
        r = httpx.get(f"{settings.QDRANT_URL}/collections", headers={"api-key": settings.QDRANT_API_KEY}, timeout=2)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _first_point(path: Path) -> dict:
    with path.open() as f:
        return json.loads(f.readline())


live = pytest.mark.skipif(not _reachable(), reason="Qdrant not reachable")


# --- pure --------------------------------------------------------------------
def test_build_filter_shapes():
    f = qdrant.build_filter({"category": "IT", "id": [1, 2], "years": {"gte": 3}, "skip": None}, must_not={"type": "chunk"})
    assert isinstance(f, models.Filter)
    assert [c.key for c in f.must] == ["category", "id", "years"]
    assert isinstance(f.must[0].match, models.MatchValue) and f.must[0].match.value == "IT"
    assert isinstance(f.must[1].match, models.MatchAny) and f.must[1].match.any == [1, 2]
    assert isinstance(f.must[2].range, models.Range) and f.must[2].range.gte == 3
    assert f.must_not[0].key == "type" and f.should is None


def test_build_filter_empty_is_none():
    assert qdrant.build_filter({"a": None}) is None
    assert qdrant.build_filter(None) is None


# --- live --------------------------------------------------------------------
@live
async def test_ping_and_collections():
    assert await qdrant.ping() is True
    names = await qdrant.list_collections()
    assert settings.QDRANT_CV_COLLECTION in names and settings.QDRANT_JD_COLLECTION in names
    assert await qdrant.count(settings.QDRANT_CV_COLLECTION) > 0


@live
@pytest.mark.skipif(not CV_POINTS.exists(), reason="pre-processing/output/points.jsonl missing")
async def test_search_resumes_roundtrip():
    rec = _first_point(CV_POINTS)
    vec, pid = rec["vector"], rec["id"]

    hits = await qdrant.search(settings.QDRANT_CV_COLLECTION, vec, limit=3)
    assert hits and hits[0].id == pid and hits[0].score > 0.99

    groups = await qdrant.search_resumes(vec, limit=3, group_size=2)
    assert 0 < len(groups) <= 3
    assert len({g.key for g in groups}) == len(groups), "groups must be distinct resumes"
    assert groups[0].hits[0].id == pid
    assert all(len(g.hits) <= 2 for g in groups)
    assert groups[0].payload.get("id") == groups[0].key

    section = groups[0].hits[0].payload["field"]
    filtered = await qdrant.search_resumes(vec, limit=3, section=section)
    assert all(h.payload["field"] == section for g in filtered for h in g.hits)

    resume_id = groups[0].key
    sections = await qdrant.get_resume(resume_id)
    assert sections and all(p.payload["id"] == resume_id for p in sections)
    assert await qdrant.count(settings.QDRANT_CV_COLLECTION, filters={"id": resume_id}) == len(sections)

    only = await qdrant.search_resumes(vec, limit=5, resume_ids=[resume_id])
    assert [g.key for g in only] == [resume_id]

    got = await qdrant.retrieve(settings.QDRANT_CV_COLLECTION, [pid])
    assert got and got[0].id == pid


@live
@pytest.mark.skipif(not JD_POINTS.exists(), reason="pre-processing/output/jd_points.jsonl missing")
async def test_search_jobs_roundtrip():
    rec = _first_point(JD_POINTS)
    vec, pid = rec["vector"], rec["id"]

    groups = await qdrant.search_jobs(vec, limit=3, point_type=None)
    assert groups and groups[0].hits[0].id == pid and groups[0].hits[0].score > 0.99

    chunks_only = await qdrant.search_jobs(vec, limit=3)  # default point_type="chunk"
    assert all(h.payload["type"] == "chunk" for g in chunks_only for h in g.hits)

    job = await qdrant.get_job(groups[0].key)
    assert job and all(p.payload["id"] == groups[0].key for p in job)
    chunk_idx = [p.payload["chunk_index"] for p in job if p.payload.get("type") == "chunk"]
    assert chunk_idx == sorted(chunk_idx)


@live
async def test_scroll_pagination():
    pts = await qdrant.scroll(settings.QDRANT_CV_COLLECTION, limit=7, max_points=20, with_payload=["id"])
    assert len(pts) == 20 and all(set(p.payload) == {"id"} for p in pts)
