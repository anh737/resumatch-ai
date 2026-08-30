"""LLM-generated evaluation datasets (retrieval queries and QA pairs).

WHAT: deterministic samplers over the source corpora (``records.json`` resumes
and ``jobs.json`` job descriptions) plus builders that ask the judge model to
write, for each sampled document, either a recruiter-style retrieval query or
a question/ground-truth pair grounded strictly in that document.

WHY: there is no human-labelled gold set for this corpus, so we synthesise one.
Grounding each query/question in a single known document gives us a free
relevance label (``relevant_id`` / ``source_id``) to score retrieval and
generation against. Determinism comes from seeded sampling over id-sorted
documents, and JSON caching under ``EVAL_DIR`` keeps repeated notebook runs
from re-spending LLM calls.
"""

from __future__ import annotations

import json
import random
from typing import Any

from .config import (
    EVAL_DIR,
    JOBS_PATH,
    JUDGE_MODEL,
    RECORDS_PATH,
    get_openai,
)

# Per-field character budget when flattening a document for the prompt; long
# free-text fields (objectives, requirement lists) get trimmed so one doc stays
# well inside a small, cheap prompt.
_TRIM_CHARS = 700

_KINDS = ("cv", "jd")


# --------------------------------------------------------------------------- #
# Corpus loading and deterministic sampling
# --------------------------------------------------------------------------- #

def _load_json(path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sample_resumes(n: int, seed: int = 42) -> list[dict]:
    """Deterministically sample ``n`` resumes (seeded, over id-sorted list)."""
    rows = sorted(_load_json(RECORDS_PATH), key=lambda r: r["id"])
    return random.Random(seed).sample(rows, n)


def sample_jobs(n: int, seed: int = 42) -> list[dict]:
    """Deterministically sample ``n`` jobs (seeded, over id-sorted list)."""
    rows = sorted(_load_json(JOBS_PATH), key=lambda r: r["id"])
    return random.Random(seed).sample(rows, n)


# --------------------------------------------------------------------------- #
# Document flattening (compact prompt text, never includes the id)
# --------------------------------------------------------------------------- #

def _trim(text: str, limit: int = _TRIM_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _cv_text(rec: dict[str, Any]) -> str:
    """Compact skills + roles + objective view of one resume record."""
    parts: list[str] = []
    skills = ", ".join(str(s) for s in rec.get("technical_skills") or [])
    if skills:
        parts.append(f"Technical skills: {_trim(skills)}")
    objective = str(rec.get("objective") or "").strip()
    if objective:
        parts.append(f"Objective: {_trim(objective)}")
    roles = "; ".join(
        f"{w.get('title', 'Unknown role')} at {w.get('company', 'unknown company')}"
        for w in rec.get("work_exp") or []
    )
    if roles:
        parts.append(f"Work experience: {_trim(roles)}")
    certs = ", ".join(str(c) for c in rec.get("certification") or [])
    if certs:
        parts.append(f"Certifications: {_trim(certs, 300)}")
    return "\n".join(parts)


def _jd_text(job: dict[str, Any]) -> str:
    """Compact title + requirements + responsibilities view of one job.

    Deliberately omits the company name: jd queries must be grounded ONLY
    in the posting's title and key requirements, and a leaked company name
    would make retrieval unrealistically easy.
    """
    parts = [f"Job title: {job.get('job_title', '')}"]
    reqs = " | ".join(str(r) for r in job.get("requirements") or [])
    if reqs:
        parts.append(f"Requirements: {_trim(reqs)}")
    resp = " | ".join(str(r) for r in job.get("responsibilities") or [])
    if resp:
        parts.append(f"Responsibilities: {_trim(resp)}")
    description = str(job.get("description") or "").strip()
    if description and not (reqs or resp):
        parts.append(f"Description: {_trim(description)}")
    return "\n".join(parts)


def _doc_text(kind: str, doc: dict[str, Any]) -> str:
    return _cv_text(doc) if kind == "cv" else _jd_text(doc)


def _check_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")


# --------------------------------------------------------------------------- #
# Structured-output helper
# --------------------------------------------------------------------------- #

def _structured(
    system: str, user: str, name: str, schema: dict, seed: int = 42
) -> dict:
    """One judge-model call with a strict json_schema response format.

    ``temperature=0`` plus the OpenAI ``seed`` parameter make rebuilds as
    repeatable as the API allows (best effort — full cross-run determinism
    is guaranteed only by the JSON cache files under ``EVAL_DIR``).
    """
    response = get_openai().chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        seed=seed,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        },
    )
    return json.loads(response.choices[0].message.content)


_QUERY_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}

_QA_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "ground_truth": {"type": "string"},
    },
    "required": ["question", "ground_truth"],
    "additionalProperties": False,
}

_QUERY_SYSTEM = {
    "cv": (
        "You write evaluation queries for a semantic search engine over "
        "candidate resumes. Given ONE resume, produce a single natural-language "
        "search phrase a recruiter would type to find exactly this candidate. "
        "Ground it ONLY in the resume's skills, past roles, and domain. Be "
        "specific enough that this resume stands out among many IT resumes. "
        "Never mention any id, name, or file."
    ),
    "jd": (
        "You write evaluation queries for a semantic search engine over job "
        "descriptions. Given ONE job posting, produce a single natural-language "
        "search phrase a job seeker would type to find exactly this job. Ground "
        "it ONLY in the posting's title and key requirements. Be specific "
        "enough that this posting stands out among many postings. Never mention "
        "any id or url."
    ),
}

_QA_SYSTEM = {
    "cv": (
        "You create question-answer pairs to evaluate a RAG system over "
        "candidate resumes. Given ONE resume, write one factual question that "
        "is answerable from this resume alone, and its concise ground-truth "
        "answer (1-3 sentences) using ONLY facts stated in the resume. Do not "
        "mention ids. The question should read like something a recruiter "
        "would ask about candidates with this profile."
    ),
    "jd": (
        "You create question-answer pairs to evaluate a RAG system over job "
        "descriptions. Given ONE job posting, write one factual question that "
        "is answerable from this posting alone, and its concise ground-truth "
        "answer (1-3 sentences) using ONLY facts stated in the posting. Do not "
        "mention ids. The question should read like something a job seeker "
        "would ask about this kind of role."
    ),
}


# --------------------------------------------------------------------------- #
# Dataset builders (cached under EVAL_DIR)
# --------------------------------------------------------------------------- #

def _load_cache(path) -> list[dict] | None:
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _save_cache(path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2, ensure_ascii=False)


def build_retrieval_dataset(
    kind: str, n: int = 40, seed: int = 42, force: bool = False
) -> list[dict]:
    """Build (or load cached) retrieval queries with known relevant document.

    Each item: ``{"query": str, "relevant_id": int|str, "kind": kind}`` where
    the query was generated by the judge model from that single document.
    """
    _check_kind(kind)
    cache = EVAL_DIR / f"retrieval_{kind}_{n}_{seed}.json"
    if not force and (cached := _load_cache(cache)) is not None:
        return cached

    docs = sample_resumes(n, seed) if kind == "cv" else sample_jobs(n, seed)
    items: list[dict] = []
    for doc in docs:
        out = _structured(
            _QUERY_SYSTEM[kind],
            _doc_text(kind, doc),
            "retrieval_query",
            _QUERY_SCHEMA,
            seed=seed,
        )
        items.append(
            {"query": out["query"], "relevant_id": doc["id"], "kind": kind}
        )
    _save_cache(cache, items)
    return items


def build_qa_dataset(
    kind: str, n: int = 15, seed: int = 42, force: bool = False
) -> list[dict]:
    """Build (or load cached) QA pairs grounded in single documents.

    Each item: ``{"question": str, "ground_truth": str, "source_id": int|str,
    "kind": kind}``.
    """
    _check_kind(kind)
    cache = EVAL_DIR / f"qa_{kind}_{n}_{seed}.json"
    if not force and (cached := _load_cache(cache)) is not None:
        return cached

    docs = sample_resumes(n, seed) if kind == "cv" else sample_jobs(n, seed)
    items: list[dict] = []
    for doc in docs:
        out = _structured(
            _QA_SYSTEM[kind], _doc_text(kind, doc), "qa_pair", _QA_SCHEMA, seed=seed
        )
        items.append(
            {
                "question": out["question"],
                "ground_truth": out["ground_truth"],
                "source_id": doc["id"],
                "kind": kind,
            }
        )
    _save_cache(cache, items)
    return items
