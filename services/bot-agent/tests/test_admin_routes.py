"""router.admin — FastAPI wiring (query parsing, route precedence) with stubbed handlers."""

from datetime import datetime, timezone

import httpx
import pytest

import router.admin as admin_router
from schemas.admin import TraceDetailOut, TracesOut, UploadOut, UploadStatsOut

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _row(upload_id="u1", kind="cv", status="done"):
    return UploadOut(id=upload_id, kind=kind, document_id="1", filename="a.pdf", status=status, created_at=NOW, updated_at=NOW)


@pytest.fixture
async def client(monkeypatch):
    calls: dict[str, object] = {}

    async def list_uploads(limit, *, kind=None, status=None):
        calls["list_uploads"] = (limit, kind, status)
        return [_row()]

    async def get_upload(upload_id):
        calls["get_upload"] = upload_id
        return _row(upload_id)

    async def get_upload_stats():
        calls["stats"] = True
        return UploadStatsOut()

    async def reprocess_upload(upload_id):
        calls["reprocess"] = upload_id
        return _row(upload_id, status="queued")

    async def list_traces(limit, *, page, tags, name):
        calls["list_traces"] = (limit, page, tags, name)
        return TracesOut(configured=True, public_url="http://lf", page=page, limit=limit)

    async def get_trace(trace_id):
        calls["get_trace"] = trace_id
        return TraceDetailOut(id=trace_id, url="http://lf/trace/" + trace_id)

    for name, fn in [("list_uploads", list_uploads), ("get_upload", get_upload), ("get_upload_stats", get_upload_stats),
                     ("reprocess_upload", reprocess_upload), ("list_traces", list_traces), ("get_trace", get_trace)]:
        monkeypatch.setattr(admin_router, name, fn)

    from main import app  # lifespan is not run by ASGITransport

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, calls


async def test_uploads_filters(client):
    c, calls = client
    r = await c.get("/admin/uploads?kind=jd&status=failed&limit=5")
    assert r.status_code == 200 and calls["list_uploads"] == (5, "jd", "failed")
    assert (await c.get("/admin/uploads?kind=pdf")).status_code == 422
    assert (await c.get("/admin/uploads?limit=0")).status_code == 422


async def test_stats_route_wins_over_upload_id(client):
    c, calls = client
    r = await c.get("/admin/uploads/stats")
    assert r.status_code == 200 and calls.get("stats") is True and "all" in r.json()
    r = await c.get("/admin/uploads/abc")
    assert r.status_code == 200 and calls["get_upload"] == "abc"
    r = await c.post("/admin/uploads/abc/reprocess")
    assert r.status_code == 202 and calls["reprocess"] == "abc" and r.json()["status"] == "queued"


async def test_traces_query_parsing(client):
    c, calls = client
    r = await c.get("/admin/traces?limit=10&page=3&tags=ingestion&tags=cv&name=ingest-cv")
    assert r.status_code == 200 and calls["list_traces"] == (10, 3, ["ingestion", "cv"], "ingest-cv")
    assert r.json()["page"] == 3
    r = await c.get("/admin/traces/t1")
    assert r.status_code == 200 and r.json()["url"].endswith("/t1") and calls["get_trace"] == "t1"


async def test_health_omits_probes_by_default(client):
    c, _ = client
    body = (await c.get("/health")).json()
    assert body == {"status": "ok", "app": "bot-agent"}
