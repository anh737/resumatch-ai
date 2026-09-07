from fastapi import APIRouter, File, Form, Query, UploadFile

from handler.admin import get_trace, get_upload, get_upload_stats, list_traces, list_uploads, reprocess_upload, submit_upload
from schemas.admin import TraceDetailOut, TracesOut, UploadKind, UploadOut, UploadStatsOut, UploadStatus

router = APIRouter(tags=["admin"])


@router.post("/admin/uploads", response_model=UploadOut, status_code=202)
async def upload(
    kind: UploadKind = Form(...),
    file: UploadFile = File(...),
    category: str | None = Form(None),
) -> UploadOut:
    return await submit_upload(kind, file, category=category)


@router.get("/admin/uploads", response_model=list[UploadOut])
async def uploads(
    limit: int = Query(50, ge=1, le=500),
    kind: UploadKind | None = None,
    status: UploadStatus | None = None,
) -> list[UploadOut]:
    return await list_uploads(limit, kind=kind, status=status)


# Declared before the `{upload_id}` route so "stats" is never taken for an id.
@router.get("/admin/uploads/stats", response_model=UploadStatsOut)
async def upload_stats() -> UploadStatsOut:
    return await get_upload_stats()


@router.get("/admin/uploads/{upload_id}", response_model=UploadOut)
async def upload_by_id(upload_id: str) -> UploadOut:
    return await get_upload(upload_id)


@router.post("/admin/uploads/{upload_id}/reprocess", response_model=UploadOut, status_code=202)
async def reprocess(upload_id: str) -> UploadOut:
    return await reprocess_upload(upload_id)


@router.get("/admin/traces", response_model=TracesOut)
async def traces(
    limit: int = Query(20, ge=1, le=100),
    page: int = Query(1, ge=1),
    name: str | None = None,
    tags: list[str] | None = Query(None),
) -> TracesOut:
    return await list_traces(limit, page=page, tags=tags, name=name)


@router.get("/admin/traces/{trace_id}", response_model=TraceDetailOut)
async def trace_by_id(trace_id: str) -> TraceDetailOut:
    return await get_trace(trace_id)
