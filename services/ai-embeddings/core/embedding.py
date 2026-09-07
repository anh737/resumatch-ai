"""Batch embedding of point drafts — same batching and retry as the notebooks.

Requests are capped at ``EMBED_BATCH_SIZE`` texts (64) or
``EMBED_BATCH_TOKENS`` tokens (250k, headroom under the API's 300k cap),
whichever fills first; transient API errors are retried with exponential
backoff. Vector order matches draft order (``core.llm.embed_texts`` re-sorts
by index).
"""

from __future__ import annotations

import asyncio
from typing import Sequence

import openai

from core.llm import embed_texts
from core.points import PointDraft
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


async def _embed_batch(texts: list[str], *, attempts: int = 4) -> list[list[float]]:
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return await embed_texts(texts)
        except RETRYABLE as exc:
            last_err = exc
            log.warning("embedding batch failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
            if attempt < attempts - 1:
                await asyncio.sleep(2**attempt)
    raise RuntimeError(f"embedding batch failed: {last_err!r}") from last_err


def _batches(drafts: Sequence[PointDraft]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for draft in drafts:
        if current and (len(current) == settings.EMBED_BATCH_SIZE or current_tokens + draft.n_tokens > settings.EMBED_BATCH_TOKENS):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(draft.text)
        current_tokens += draft.n_tokens
    if current:
        batches.append(current)
    return batches


async def embed_drafts(drafts: Sequence[PointDraft]) -> list[list[float]]:
    """Embed every draft's text; result[i] is the vector for drafts[i]."""
    vectors: list[list[float]] = []
    for batch in _batches(drafts):
        vectors.extend(await _embed_batch(batch))
    if len(vectors) != len(drafts):
        raise RuntimeError(f"{len(vectors)} vectors for {len(drafts)} drafts")
    return vectors
