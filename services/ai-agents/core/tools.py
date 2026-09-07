"""Agent tools — retrieval and lookup over the resume / job-description knowledge base.

Four domain tools plus the ``finish_tool_calls`` terminator (project
architecture §5):

* ``retrieval_cv`` / ``retrieval_jd`` — semantic search: embed the query with
  ``core.llm.embed_text`` and run a grouped Qdrant search; results are compact
  JSON-safe dicts sized for an LLM context, not full payload dumps.
* ``get_resume`` / ``get_job`` — deterministic lookup of ONE document by its id
  or by the file name it was uploaded with through the admin portal
  (``payload.source``, written by ai-embeddings). A file name is an identifier,
  not a search phrase: embedding it and taking the nearest neighbour returns
  an arbitrary document, which is exactly the failure these tools prevent.
  They return the full structured record, or ``found: false`` together with
  the documents that do exist so the answer step can say so honestly.

``TOOL_SPECS`` is the OpenAI ``tools=[...]`` schema list; :func:`execute_tool`
dispatches one call by name (raising ``UnknownToolError`` for anything else) so
``core.agent`` stays free of per-tool wiring.
"""

from __future__ import annotations

import difflib
from pathlib import PurePosixPath
from typing import Any, Literal

from core.llm import embed_text
from services.observability import langfuse
from services.vector_store import qdrant
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

FINISH_TOOL = "finish_tool_calls"

# Longest text snippet forwarded to the LLM per matching section/chunk.
_SNIPPET_CHARS = 600
# Cap on any single text value inside a full record returned by get_resume / get_job.
_RECORD_TEXT_CHARS = 6000
# Lowest difflib ratio accepted when a file name only matches approximately.
_FUZZY_MIN_RATIO = 0.6
# Structured JD fields copied from a `type=field` point when one matched.
_JD_RECORD_FIELDS = (
    "job_title", "company", "location", "salary", "employment_type",
    "requirements", "responsibilities", "benefits",
)
_JD_FULL_FIELDS = (
    "job_title", "company", "location", "salary", "employment_type",
    "description", "requirements", "responsibilities", "benefits",
)
_CV_RECORD_FIELDS = ("objective", "work_exp", "information", "certification", "technical_skills")
_KNOWN_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

Kind = Literal["cv", "jd"]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "retrieval_cv",
            "description": (
                "Semantic search over candidate resumes (CVs). Returns the best-matching "
                "candidates, each with its most relevant resume sections. Use for questions "
                "about candidates, profiles, skills, experience, or who fits a job. NOT for a "
                "specific file name or resume id — use get_resume for those."
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
                    "filename": {
                        "type": "string",
                        "description": "Restrict to the resume uploaded with this exact file name.",
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
                "Use for questions about jobs, vacancies, requirements, or which role fits a "
                "profile. NOT for a specific file name or job id — use get_job for those."
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
                    "filename": {
                        "type": "string",
                        "description": "Restrict to the job posting uploaded with this exact file name.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_resume",
            "description": (
                "Fetch ONE specific resume in full — by resume_id (e.g. 123 for 'resume #123' "
                "from an earlier answer) or by the file name it was uploaded with (e.g. "
                "'BuiNgocAnh_ML_Engineer_2026.pdf'; extension optional, case-insensitive). "
                "Always use this, never retrieval_cv, when the user refers to a particular "
                "file, 'the CV I uploaded', or a resume id. Returns found=false plus the list "
                "of uploaded files when nothing matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "integer", "description": "Resume id, e.g. 123 for 'resume #123'."},
                    "filename": {
                        "type": "string",
                        "description": "File name the resume was uploaded with (extension optional).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_job",
            "description": (
                "Fetch ONE specific job description in full — by job_id (e.g. 'upload_1725270000000' "
                "for 'job #upload_1725270000000') or by the file name it was uploaded with. Always "
                "use this, never retrieval_jd, when the user refers to a particular file, 'the JD I "
                "uploaded', or a job id. Returns found=false plus the list of uploaded files when "
                "nothing matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job id, e.g. 'upload_1725270000000'."},
                    "filename": {
                        "type": "string",
                        "description": "File name the job posting was uploaded with (extension optional).",
                    },
                },
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


def _clip(value: Any) -> Any:
    """Cap long strings inside a full record (recursively) so one document cannot flood the context."""
    if isinstance(value, str):
        return value if len(value) <= _RECORD_TEXT_CHARS else value[:_RECORD_TEXT_CHARS] + "…"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


def filename_stem(name: str) -> str:
    """Lookup key of a file name: base name, lower-cased, known extension dropped (same rule as ai-embeddings)."""
    base = PurePosixPath(str(name).strip().replace("\\", "/")).name
    path = PurePosixPath(base)
    stem = path.stem if path.suffix.lower() in _KNOWN_EXTENSIONS else base
    return " ".join(stem.lower().split())


def _source_info(payload: dict[str, Any]) -> dict[str, Any] | None:
    source = payload.get("source")
    if not isinstance(source, dict):
        return None
    return {k: source.get(k) for k in ("filename", "upload_id", "uploaded_at", "ingested_at")}


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(str(value).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------
async def retrieval_cv(
    query: str,
    top_k: int | None = None,
    section: str | None = None,
    category: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Top matching resumes for ``query``; one entry per candidate."""
    vector = await embed_text(query)
    groups = await qdrant.search_resumes(
        vector,
        limit=top_k or settings.AGENT_RETRIEVAL_TOP_K,
        section=section,
        category=category,
        filename=filename or None,
    )
    results = [
        {
            "resume_id": g.key,
            "score": round(g.score, 4),
            "category": g.payload.get("category"),
            **({"source": _source_info(g.payload)} if _source_info(g.payload) else {}),
            "sections": [
                {"field": h.payload.get("field"), "text": _snippet(h.payload.get("embedded_text"))}
                for h in g.hits
            ],
        }
        for g in groups
    ]
    langfuse.log_retrieval(
        "retrieval_cv",
        query={"query": query, "top_k": top_k, "section": section, "category": category, "filename": filename},
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
    filename: str | None = None,
) -> dict[str, Any]:
    """Top matching job descriptions for ``query``; one entry per job."""
    vector = await embed_text(query)
    groups = await qdrant.search_jobs(
        vector,
        limit=top_k or settings.AGENT_RETRIEVAL_TOP_K,
        company=company,
        employment_type=employment_type,
        filename=filename or None,
        point_type=None,  # match chunk and field points so structured records can surface
    )
    results = []
    for g in groups:
        entry: dict[str, Any] = {"job_id": g.key, "score": round(g.score, 4)}
        record = next((h.payload for h in g.hits if h.payload.get("type") == qdrant.JOB_TYPE_FIELD), None)
        if record:
            entry.update({k: record[k] for k in _JD_RECORD_FIELDS if record.get(k) not in (None, "", [])})
        if _source_info(g.payload):
            entry["source"] = _source_info(g.payload)
        entry["matched_text"] = [_snippet(h.payload.get("embedded_text")) for h in g.hits]
        results.append(entry)
    langfuse.log_retrieval(
        "retrieval_jd",
        query={"query": query, "top_k": top_k, "company": company, "employment_type": employment_type, "filename": filename},
        results=results,
        metadata={"collection": settings.QDRANT_JD_COLLECTION, "hits": len(results)},
    )
    log.debug("retrieval_jd %r -> %d jobs", query, len(results))
    return {"query": query, "results": results}


# ---------------------------------------------------------------------------
# Lookup of one document (by id or upload file name)
# ---------------------------------------------------------------------------
async def _uploaded_documents(kind: Kind) -> list[dict[str, Any]]:
    """One entry per uploaded document (newest first): id, filename, stem, uploaded_at (+ title for jobs)."""
    points = await (qdrant.list_uploaded_resumes() if kind == "cv" else qdrant.list_uploaded_jobs())
    docs: dict[Any, dict[str, Any]] = {}
    for point in points:
        doc_id = point.payload.get("id")
        source = point.payload.get("source") or {}
        if doc_id is None or doc_id in docs:
            continue
        entry = {
            "id": doc_id,
            "filename": source.get("filename"),
            "stem": source.get("stem") or filename_stem(source.get("filename") or ""),
            "uploaded_at": source.get("uploaded_at") or source.get("ingested_at"),
        }
        if kind == "jd":
            entry.update(job_title=point.payload.get("job_title"), company=point.payload.get("company"))
        docs[doc_id] = entry
    return sorted(docs.values(), key=lambda d: d["uploaded_at"] or "", reverse=True)


async def _resolve_by_filename(kind: Kind, filename: str) -> tuple[list[qdrant.Point], str | None, list[dict[str, Any]]]:
    """Exact file name -> lookup stem -> fuzzy match against the uploaded documents.

    Returns ``(points, matched_by, candidates)``; ``candidates`` lists the
    closest uploaded files (best first) whenever an approximate step ran.
    """
    finder = qdrant.find_resumes_by_source if kind == "cv" else qdrant.find_jobs_by_source
    wanted = str(filename).strip()
    if not wanted:
        return [], None, []
    points = await finder(filename=wanted)
    if points:
        return points, "filename", []
    stem = filename_stem(wanted)
    if stem:
        points = await finder(stem=stem)
        if points:
            return points, "filename", []

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in await _uploaded_documents(kind):
        doc_stem = doc["stem"] or ""
        ratio = difflib.SequenceMatcher(None, stem, doc_stem).ratio() if stem and doc_stem else 0.0
        if stem and doc_stem and (stem in doc_stem or doc_stem in stem):
            ratio = max(ratio, 0.9)
        scored.append((ratio, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [{"id": d["id"], "filename": d["filename"], "similarity": round(r, 2)} for r, d in scored[:5]]
    if scored and scored[0][0] >= _FUZZY_MIN_RATIO:
        best = scored[0][1]
        points = await (qdrant.get_resume(int(best["id"])) if kind == "cv" else qdrant.get_job(str(best["id"])))
        if points:
            return points, "fuzzy", candidates
    return [], None, candidates


def _uploaded_files_out(kind: Kind, docs: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    key = "resume_id" if kind == "cv" else "job_id"
    out = []
    for d in docs[:limit]:
        entry = {key: d["id"], "filename": d["filename"], "uploaded_at": d["uploaded_at"]}
        if kind == "jd":
            entry.update(job_title=d.get("job_title"), company=d.get("company"))
        out.append(entry)
    return out


async def get_resume(resume_id: int | str | None = None, filename: str | None = None) -> dict[str, Any]:
    """One resume in full, by id or by upload file name (see module docstring)."""
    rid = _int_or_none(resume_id)
    if rid is None and not (filename and str(filename).strip()):
        raise ValueError("get_resume needs resume_id or filename")
    points: list[qdrant.Point] = []
    matched_by: str | None = None
    candidates: list[dict[str, Any]] = []
    if rid is not None:
        points = await qdrant.get_resume(rid)
        matched_by = "resume_id" if points else None
    if not points and filename:
        points, matched_by, candidates = await _resolve_by_filename("cv", filename)

    if points:
        payload = points[0].payload
        result: dict[str, Any] = {
            "found": True,
            "matched_by": matched_by,
            "resume_id": payload.get("id"),
            "category": payload.get("category"),
            "source": _source_info(payload),
            "sections_indexed": sorted({p.payload.get("field") for p in points if p.payload.get("field")}),
            "record": _clip({k: payload.get(k) for k in _CV_RECORD_FIELDS}),
        }
        if matched_by == "fuzzy":
            result["other_candidates"] = candidates[1:]
    else:
        result = {
            "found": False,
            "resume_id": rid,
            "filename": filename,
            "closest_files": candidates,
            "uploaded_files": _uploaded_files_out("cv", await _uploaded_documents("cv")),
            "note": "No resume with this id / file name is in the knowledge base. Tell the user so; do not substitute a similar candidate.",
        }
    langfuse.log_retrieval(
        "get_resume",
        query={"resume_id": rid, "filename": filename},
        results=result,
        metadata={"collection": settings.QDRANT_CV_COLLECTION, "found": result["found"], "matched_by": matched_by},
    )
    log.debug("get_resume id=%s file=%r -> found=%s (%s)", rid, filename, result["found"], matched_by)
    return result


async def get_job(job_id: str | None = None, filename: str | None = None) -> dict[str, Any]:
    """One job description in full, by id or by upload file name."""
    jid = str(job_id).strip().lstrip("#") if job_id not in (None, "") else None
    if not jid and not (filename and str(filename).strip()):
        raise ValueError("get_job needs job_id or filename")
    points: list[qdrant.Point] = []
    matched_by: str | None = None
    candidates: list[dict[str, Any]] = []
    if jid:
        points = await qdrant.get_job(jid)
        matched_by = "job_id" if points else None
    if not points and filename:
        points, matched_by, candidates = await _resolve_by_filename("jd", filename)

    if points:
        payload = next((p.payload for p in points if p.payload.get("type") == qdrant.JOB_TYPE_FIELD), points[0].payload)
        result: dict[str, Any] = {
            "found": True,
            "matched_by": matched_by,
            "job_id": payload.get("id"),
            "source": _source_info(payload),
            "record": _clip({k: payload.get(k) for k in _JD_FULL_FIELDS}),
        }
        if matched_by == "fuzzy":
            result["other_candidates"] = candidates[1:]
    else:
        result = {
            "found": False,
            "job_id": jid,
            "filename": filename,
            "closest_files": candidates,
            "uploaded_files": _uploaded_files_out("jd", await _uploaded_documents("jd")),
            "note": "No job description with this id / file name is in the knowledge base. Tell the user so; do not substitute a similar job.",
        }
    langfuse.log_retrieval(
        "get_job",
        query={"job_id": jid, "filename": filename},
        results=result,
        metadata={"collection": settings.QDRANT_JD_COLLECTION, "found": result["found"], "matched_by": matched_by},
    )
    log.debug("get_job id=%s file=%r -> found=%s (%s)", jid, filename, result["found"], matched_by)
    return result


_EXECUTORS = {"retrieval_cv": retrieval_cv, "retrieval_jd": retrieval_jd, "get_resume": get_resume, "get_job": get_job}


async def execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Run one tool call. ``finish_tool_calls`` is a no-op; unknown names raise."""
    if name == FINISH_TOOL:
        return None
    executor = _EXECUTORS.get(name)
    if executor is None:
        raise UnknownToolError(name)
    return await executor(**arguments)
