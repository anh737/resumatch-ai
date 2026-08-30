"""Agent tools — retrieval over the resume / job-description knowledge base.

Two domain tools plus the ``finish_tool_calls`` terminator (project
architecture §5). Each tool embeds the query with ``core.llm.embed_text`` and
searches Qdrant through ``services.vector_store.qdrant``; results are compact
JSON-safe dicts sized for an LLM context, not full payload dumps.

``TOOL_SPECS`` is the OpenAI ``tools=[...]`` schema list; :func:`execute_tool`
dispatches one call by name (raising ``UnknownToolError`` for anything else) so
``core.agent`` stays free of per-tool wiring.
"""

from __future__ import annotations

from typing import Any

from core.llm import embed_text
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

FINISH_TOOL = "finish_tool_calls"

# Longest text snippet forwarded to the LLM per matching section/chunk.
_SNIPPET_CHARS = 600
# Structured JD fields copied from a `type=field` point when one matched.
_JD_RECORD_FIELDS = (
    "job_title", "company", "location", "salary", "employment_type",
    "requirements", "responsibilities", "benefits",
)

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "retrieval_cv",
            "description": (
                "Semantic search over candidate resumes (CVs). Returns the best-matching "
                "candidates, each with its most relevant resume sections. Use for questions "
                "about candidates, profiles, skills, experience, or who fits a job."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Self-contained search phrase describing the wanted candidate/skills.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": f"Number of candidates to return (default {settings.AGENT_RETRIEVAL_TOP_K}).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "section": {
                        "type": "string",
                        "enum": ["information", "objective", "work_exp", "technical_skills", "certification"],
                        "description": "Restrict the search to one resume section.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Restrict to a resume category, e.g. 'INFORMATION-TECHNOLOGY'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieval_jd",
            "description": (
                "Semantic search over job descriptions (JDs). Returns the best-matching jobs "
                "with their structured record (title, company, requirements, ...) when stored. "
                "Use for questions about jobs, vacancies, requirements, or which role fits a profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Self-contained search phrase describing the wanted job/role.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": f"Number of jobs to return (default {settings.AGENT_RETRIEVAL_TOP_K}).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "company": {"type": "string", "description": "Restrict to one company name."},
                    "employment_type": {
                        "type": "string",
                        "description": "Restrict to an employment type, e.g. 'Full Time'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": FINISH_TOOL,
            "description": (
                "REQUIRED terminator. Call this (alone, no other tool in the same round) when "
                "enough information has been gathered to answer, or when no tool fits the request."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class UnknownToolError(Exception):
    """The model asked for a tool that is not registered."""


def _snippet(text: Any) -> str:
    text = str(text or "").strip()
    return text[:_SNIPPET_CHARS] + ("…" if len(text) > _SNIPPET_CHARS else "")


async def retrieval_cv(
    query: str,
    top_k: int | None = None,
    section: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Top matching resumes for ``query``; one entry per candidate."""
    vector = await embed_text(query)
    groups = await qdrant.search_resumes(
        vector,
        limit=top_k or settings.AGENT_RETRIEVAL_TOP_K,
        section=section,
        category=category,
    )
    results = [
        {
            "resume_id": g.key,
            "score": round(g.score, 4),
            "category": g.payload.get("category"),
            "sections": [
                {"field": h.payload.get("field"), "text": _snippet(h.payload.get("embedded_text"))}
                for h in g.hits
            ],
        }
        for g in groups
    ]
    langfuse.log_retrieval(
        "retrieval_cv",
        query={"query": query, "top_k": top_k, "section": section, "category": category},
        results=results,
        metadata={"collection": settings.QDRANT_CV_COLLECTION, "hits": len(results)},
    )
    log.debug("retrieval_cv %r -> %d resumes", query, len(results))
    return {"query": query, "results": results}


async def retrieval_jd(
    query: str,
    top_k: int | None = None,
    company: str | None = None,
    employment_type: str | None = None,
) -> dict[str, Any]:
    """Top matching job descriptions for ``query``; one entry per job."""
    vector = await embed_text(query)
    groups = await qdrant.search_jobs(
        vector,
        limit=top_k or settings.AGENT_RETRIEVAL_TOP_K,
        company=company,
        employment_type=employment_type,
        point_type=None,  # match chunk and field points so structured records can surface
    )
    results = []
    for g in groups:
        entry: dict[str, Any] = {"job_id": g.key, "score": round(g.score, 4)}
        record = next((h.payload for h in g.hits if h.payload.get("type") == qdrant.JOB_TYPE_FIELD), None)
        if record:
            entry.update({k: record[k] for k in _JD_RECORD_FIELDS if record.get(k) not in (None, "", [])})
        entry["matched_text"] = [_snippet(h.payload.get("embedded_text")) for h in g.hits]
        results.append(entry)
    langfuse.log_retrieval(
        "retrieval_jd",
        query={"query": query, "top_k": top_k, "company": company, "employment_type": employment_type},
        results=results,
        metadata={"collection": settings.QDRANT_JD_COLLECTION, "hits": len(results)},
    )
    log.debug("retrieval_jd %r -> %d jobs", query, len(results))
    return {"query": query, "results": results}


_EXECUTORS = {"retrieval_cv": retrieval_cv, "retrieval_jd": retrieval_jd}


async def execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Run one tool call. ``finish_tool_calls`` is a no-op; unknown names raise."""
    if name == FINISH_TOOL:
        return None
    executor = _EXECUTORS.get(name)
    if executor is None:
        raise UnknownToolError(name)
    return await executor(**arguments)
