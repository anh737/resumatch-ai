"""Qdrant point construction — the exact schema of the offline notebooks.

The chatbot (``ai-agents``) and the evaluation pipeline read these payloads, so
every key, id scheme and truncation rule here must match
``pre-processing/preprocessing_cv.ipynb`` / ``preprocess_jd.ipynb``:

* CV (``cv_information_technology``): one point per non-empty field of
  ``objective | work_exp | information | certification | technical_skills``,
  point id ``uuid5(NAMESPACE_URL, "cv:{resume_id}:{field}")``, payload
  ``{id:int, field, category, embedded_text, <full record>}``.
* JD (``jd_jobs``): one point per semantic chunk
  (``uuid5("jd_chunk:{job_id}:{index}")``, payload ``{type:"chunk",
  chunk_index, embedded_text, <full job record>}``) plus one point per
  non-empty field of ``description | requirements | responsibilities``
  (``uuid5("jd_field:{job_id}:{field}")``, payload ``{type:"field", field,
  embedded_text, <full job record>}``). ``benefits`` never gets a field point.

``embedded_text`` is by construction the exact string that gets embedded:
texts are truncated ONCE (tiktoken ``cl100k_base``, ``MAX_EMBED_TOKENS``)
before both the payload and the embeddings request.

Online uploads add ONE extra key the offline corpus does not have: ``source``
(``{filename, stem, upload_id, bucket, key, content_type, uploaded_at,
ingested_at}``), so the chatbot can fetch a document by the file name it was
uploaded with (``ai-agents`` ``get_resume`` / ``get_job``). It is appended after
the notebook keys and indexed on ``source.filename`` / ``source.stem`` /
``source.upload_id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

import tiktoken

from setting import settings

CV_EMBED_FIELDS = ["objective", "work_exp", "information", "certification", "technical_skills"]
JOB_EMBED_FIELDS = ["description", "requirements", "responsibilities"]

# Indexes for the upload metadata (nested keys use Qdrant's dot notation).
SOURCE_PAYLOAD_INDEXES = {"source.filename": "keyword", "source.stem": "keyword", "source.upload_id": "keyword"}

# Payload indexes the notebooks create — ensure_collection() applies the same (+ source).
CV_PAYLOAD_INDEXES = {"field": "keyword", "id": "integer", "category": "keyword", **SOURCE_PAYLOAD_INDEXES}
JD_PAYLOAD_INDEXES = {
    "type": "keyword",
    "field": "keyword",
    "id": "keyword",
    "company": "keyword",
    "employment_type": "keyword",
    **SOURCE_PAYLOAD_INDEXES,
}

_KNOWN_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def filename_stem(filename: str) -> str:
    """Case-insensitive lookup key for a file name: lower-cased, extension dropped."""
    name = PurePosixPath(str(filename).strip().replace("\\", "/")).name
    path = PurePosixPath(name)
    stem = path.stem if path.suffix.lower() in _KNOWN_EXTENSIONS else name
    return " ".join(stem.lower().split())


def source_payload(
    *,
    filename: str,
    upload_id: str,
    bucket: str,
    key: str,
    content_type: str | None = None,
    uploaded_at: str | None = None,
) -> dict[str, Any]:
    """The ``source`` payload block of an online-ingested document."""
    return {
        "filename": filename,
        "stem": filename_stem(filename),
        "upload_id": upload_id,
        "bucket": bucket,
        "key": key,
        "content_type": content_type,
        "uploaded_at": uploaded_at,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@dataclass(slots=True)
class PointDraft:
    """A point ready to embed: deterministic id, final payload, exact text."""

    id: str
    payload: dict[str, Any]
    text: str
    n_tokens: int


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")  # tokenizer of text-embedding-3-*


def truncate(text: str) -> tuple[str, int]:
    """Truncate ONCE so the payload's embedded_text is exactly what gets embedded."""
    tokens = _encoding().encode(text)
    if len(tokens) > settings.MAX_EMBED_TOKENS:
        tokens = tokens[: settings.MAX_EMBED_TOKENS]
        return _encoding().decode(tokens), settings.MAX_EMBED_TOKENS
    return text, len(tokens)


# ---------------------------------------------------------------------------
# CV points
# ---------------------------------------------------------------------------
def render_job(job: dict[str, Any]) -> str:
    head = " | ".join(
        str(x)
        for x in [
            job.get("title"),
            job.get("company"),
            f"{job.get('start_date') or '?'} - {job.get('end_date') or '?'}",
            job.get("location"),
        ]
        if x
    )
    return f"{head}: {job.get('description') or ''}".strip(" :")


def render_field(record: dict[str, Any], field: str) -> str | None:
    """One CV field -> the text to embed (same rendering as the notebook)."""
    value = record.get(field)
    if not value:
        return None
    if field == "work_exp":
        return "\n".join(render_job(j) for j in value) or None
    if field == "information" and isinstance(value, dict):
        edu = "; ".join(
            " ".join(str(p) for p in [e.get("degree"), e.get("field"), e.get("institution"), e.get("year")] if p)
            for e in value.get("education", [])
        )
        parts = [p for p in [edu, ", ".join(value.get("languages", [])), value.get("other")] if p]
        return "\n".join(parts) or None
    if isinstance(value, list):
        return "; ".join(value) or None
    return str(value).strip() or None


def build_cv_points(
    record: dict[str, Any], *, category: str, source: dict[str, Any] | None = None
) -> list[PointDraft]:
    """Structured resume record (with int ``id``) -> one draft per non-empty field.

    ``source`` (see :func:`source_payload`) is appended to every payload for
    online uploads; offline corpus points have no such key.
    """
    resume_id = int(record["id"])
    extra = {"source": source} if source else {}
    drafts = []
    for field in CV_EMBED_FIELDS:
        text = render_field(record, field)
        if not text:
            continue
        text, n_tokens = truncate(text)
        drafts.append(
            PointDraft(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"cv:{resume_id}:{field}")),
                payload={
                    "id": resume_id,
                    "field": field,
                    "category": category,
                    "embedded_text": text,
                    **{f: record.get(f) for f in CV_EMBED_FIELDS},
                    **extra,
                },
                text=text,
                n_tokens=n_tokens,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# JD points
# ---------------------------------------------------------------------------
def as_text(value: Any) -> str:
    return "; ".join(value) if isinstance(value, list) else (value or "")


def job_to_text(job: dict[str, Any]) -> str:
    """Concatenate every field of one JD into a single labeled text (chunker input)."""
    parts = []
    for field in (
        "job_title",
        "company",
        "location",
        "salary",
        "employment_type",
        "description",
        "requirements",
        "responsibilities",
        "benefits",
    ):
        value = as_text(job[field]).strip()
        if value:
            parts.append(f"{field.replace('_', ' ').title()}: {value}")
    return "\n".join(parts)


def build_jd_points(
    job: dict[str, Any], chunks: list[str], *, source: dict[str, Any] | None = None
) -> list[PointDraft]:
    """Normalized job record (string ``id``) + its semantic chunks -> drafts (``source`` as for CVs)."""
    job_id = job["id"]
    extra = {"source": source} if source else {}
    drafts = []
    for index, chunk in enumerate(chunks):
        text, n_tokens = truncate(chunk.strip())
        if not text:
            continue
        drafts.append(
            PointDraft(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"jd_chunk:{job_id}:{index}")),
                payload={"type": "chunk", "chunk_index": index, "embedded_text": text, **job, **extra},
                text=text,
                n_tokens=n_tokens,
            )
        )
    for field in JOB_EMBED_FIELDS:
        text = as_text(job[field]).strip()
        if not text:
            continue
        text, n_tokens = truncate(text)
        drafts.append(
            PointDraft(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"jd_field:{job_id}:{field}")),
                payload={"type": "field", "field": field, "embedded_text": text, **job, **extra},
                text=text,
                n_tokens=n_tokens,
            )
        )
    return drafts
