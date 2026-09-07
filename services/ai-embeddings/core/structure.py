"""LLM structuring of raw CV/JD text into the offline pipeline's record shapes.

CVs use the exact JSON schema and rules of ``pre-processing/preprocessing_cv.ipynb``
(minus the regex draft, which only exists for the HTML corpus). JDs — parsed
offline by a deterministic heading classifier over crawled HTML — are structured
here by the same LLM into the notebook's normalized job record, since an
uploaded file has no HTML structure to classify.

Both calls use strict ``json_schema`` structured output at temperature 0, so
parsing can never fail on format, and are logged to Langfuse as generations.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import openai

from core.llm import get_async_llm_client
from prompt import load_prompt
from services.observability import langfuse
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)

# ---------------------------------------------------------------------------
# Schemas (CV: verbatim from the notebook)
# ---------------------------------------------------------------------------
_JOB = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": ["string", "null"]},
        "company": {"type": ["string", "null"]},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
    },
    "required": ["title", "company", "start_date", "end_date", "location", "description"],
}

_EDU = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "degree": {"type": ["string", "null"]},
        "field": {"type": ["string", "null"]},
        "institution": {"type": ["string", "null"]},
        "year": {"type": ["string", "null"]},
    },
    "required": ["degree", "field", "institution", "year"],
}

CV_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "objective": {"type": ["string", "null"]},
        "work_exp": {"type": "array", "items": _JOB},
        "information": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "education": {"type": "array", "items": _EDU},
                "languages": {"type": "array", "items": {"type": "string"}},
                "other": {"type": ["string", "null"]},
            },
            "required": ["education", "languages", "other"],
        },
        "certification": {"type": "array", "items": {"type": "string"}},
        "technical_skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objective", "work_exp", "information", "certification", "technical_skills"],
}

JD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "job_title": {"type": ["string", "null"]},
        "company": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "salary": {"type": ["string", "null"]},
        "employment_type": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
        "benefits": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "job_title",
        "company",
        "location",
        "salary",
        "employment_type",
        "description",
        "requirements",
        "responsibilities",
        "benefits",
    ],
}

# same canonical employment types as the notebook's norm_employment_type
_ETYPE_MAP = {
    "fulltime": "Full-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Internship",
    "volunteer": "Volunteer",
    "perdiem": "Per diem",
}


def norm_employment_type(value: str | None) -> str:
    """Canonical employment type(s), same as the notebook: ``"fulltime, contract"`` -> ``"Full-time, Contract"``."""
    if not value:
        return ""
    out: list[str] = []
    for part in re.split(r"[,/]", str(value)):
        part = part.strip()
        if not part:
            continue
        out.append(_ETYPE_MAP.get(re.sub(r"[^a-z]", "", part.lower()), part))
    return ", ".join(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
async def _extract(name: str, schema: dict[str, Any], system_prompt: str, text: str, *, attempts: int = 5) -> dict[str, Any]:
    model = settings.OPENAI_STRUCTURE_MODEL
    with langfuse.log_generation(name, model=model, input={"chars": len(text)}) as gen:
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await get_async_llm_client().chat.completions.create(
                    model=model,
                    temperature=0,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": name, "strict": True, "schema": schema},
                    },
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                )
                break
            except RETRYABLE as exc:
                last_err = exc
                log.warning("%s failed (attempt %d/%d): %s", name, attempt + 1, attempts, exc)
                if attempt < attempts - 1:
                    await asyncio.sleep(min(30, 2 * 2**attempt))
        else:
            raise RuntimeError(f"{name} failed: {last_err!r}") from last_err

        choice = resp.choices[0]
        if choice.message.refusal:
            raise RuntimeError(f"{name} refused: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise RuntimeError(f"{name} output truncated (finish_reason=length)")
        record = json.loads(choice.message.content)
        gen.update(output=record, usage_details=langfuse.usage_from_openai(resp.usage))
    return record


async def structure_cv(text: str) -> dict[str, Any]:
    """Resume plain text -> the notebook's record shape (caller attaches ``id``)."""
    return await _extract("cv_extraction", CV_SCHEMA, load_prompt("cv_extraction"), text)


async def structure_jd(text: str, *, job_id: str) -> dict[str, Any]:
    """Job-posting plain text -> the notebook's normalized job record.

    Key order and value defaults match ``preprocess_jd.ipynb``'s ``normalize()``:
    strings default to ``""``, section buckets to ``[]``.
    """
    out = await _extract("jd_extraction", JD_SCHEMA, load_prompt("jd_extraction"), text)
    return {
        "id": job_id,
        "job_title": out.get("job_title") or "",
        "company": out.get("company") or "",
        "location": out.get("location") or "",
        "salary": out.get("salary") or "",
        "description": out.get("description") or "",
        "requirements": out.get("requirements") or [],
        "responsibilities": out.get("responsibilities") or [],
        "benefits": out.get("benefits") or [],
        "employment_type": norm_employment_type(out.get("employment_type")),
    }
