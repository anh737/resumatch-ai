"""Langfuse read-side adapter — the admin portal's window into observability.

ai-agents and ai-embeddings *write* traces with the Langfuse SDK; bot-agent
only needs to *read* them, so this module talks to the public REST API with
basic auth (public key / secret key) and never ships the keys to the browser —
the admin portal calls ``GET /admin/traces`` on bot-agent instead.

Endpoints used:

* ``GET /api/public/projects``          -> project id (cached) for exact deep links
* ``GET /api/public/traces``            -> recent traces, filterable by ``name`` / ``tags``
* ``GET /api/public/traces/{trace_id}`` -> one trace with its observations and scores

Typical use::

    if langfuse.enabled():
        traces, meta = await langfuse.list_traces(limit=20, tags=["ingestion"])
        detail = await langfuse.get_trace(traces[0]["id"])
        url = langfuse.trace_url(traces[0]["id"], html_path=traces[0].get("htmlPath"))
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0

# Id of the Langfuse project the keys belong to. Filled lazily by
# :func:`project_id`; used to build ``/project/<id>/traces/<trace>`` links.
_project_id: str | None = None


def enabled() -> bool:
    """True when both API keys are configured."""
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


def public_url() -> str:
    """Browser-facing Langfuse URL (the in-network LANGFUSE_HOST is not routable there)."""
    return settings.LANGFUSE_PUBLIC_URL.rstrip("/")


def trace_url(trace_id: str, *, html_path: str | None = None) -> str:
    """Deep link to one trace in the Langfuse UI.

    Prefers the ``htmlPath`` the API returns with each trace, then the cached
    project id, and finally Langfuse's ``/trace/<id>`` redirect route.
    """
    base = public_url()
    if html_path:
        return f"{base}{html_path if html_path.startswith('/') else '/' + html_path}"
    if _project_id:
        return f"{base}/project/{_project_id}/traces/{trace_id}"
    return f"{base}/trace/{trace_id}"


async def _get(path: str, params: Iterable[tuple[str, str]] | None = None) -> Any:
    if not enabled():
        raise RuntimeError("Langfuse keys are not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{settings.LANGFUSE_HOST.rstrip('/')}{path}",
            params=list(params or []),
            auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
        )
        response.raise_for_status()
        return response.json()


async def project_id() -> str | None:
    """Id of the project behind the configured keys (cached; ``None`` when unavailable)."""
    global _project_id
    if _project_id is not None or not enabled():
        return _project_id
    try:
        payload = await _get("/api/public/projects")
        items = payload.get("data") or []
        if items and items[0].get("id"):
            _project_id = str(items[0]["id"])
            log.info("Langfuse project resolved: %s", _project_id)
    except Exception as exc:  # noqa: BLE001 — links fall back to the redirect route
        log.warning("could not resolve the Langfuse project id: %s", exc)
    return _project_id


async def list_traces(
    *,
    limit: int = 20,
    page: int = 1,
    tags: Iterable[str] | None = None,
    name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Most recent traces (newest first) as raw API dicts, plus the paging ``meta``.

    Raises ``httpx.HTTPError`` on transport failures and ``RuntimeError`` when
    the keys are missing — callers decide how to degrade.
    """
    params: list[tuple[str, str]] = [("limit", str(limit)), ("page", str(page)), ("orderBy", "timestamp.DESC")]
    if name:
        params.append(("name", name))
    for tag in tags or []:
        params.append(("tags", tag))
    payload = await _get("/api/public/traces", params)
    traces = payload.get("data") or []
    log.debug("fetched %d Langfuse traces (page %d)", len(traces), page)
    return traces, payload.get("meta") or {}


async def get_trace(trace_id: str) -> dict[str, Any] | None:
    """One trace with its ``observations`` and ``scores``; ``None`` when unknown."""
    try:
        return await _get(f"/api/public/traces/{trace_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


async def ping() -> bool:
    """True if the Langfuse API answers with our keys (used by /health)."""
    if not enabled():
        return False
    try:
        await list_traces(limit=1)
        return True
    except Exception as exc:  # noqa: BLE001 — health probes must never raise
        log.warning("Langfuse ping failed: %s", exc)
        return False
