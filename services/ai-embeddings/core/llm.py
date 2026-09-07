"""Khởi tạo LLM client dùng chung cho các agent (OpenAI)."""

from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from openai import AsyncOpenAI, OpenAI

from setting import settings


@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    return OpenAI(api_key=settings.OPENAI_API_KEY)


@lru_cache(maxsize=1)
def get_async_llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def embed_texts(texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
    """Embed ``texts`` with the same model the offline pipeline used (1536-d by default).

    Order of the result matches ``texts``. Use the vectors with ``services.vector_store.qdrant``.
    """
    if not texts:
        return []
    resp = await get_async_llm_client().embeddings.create(model=model or settings.OPENAI_EMBEDDING_MODEL, input=list(texts))
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


async def embed_text(text: str, *, model: str | None = None) -> list[float]:
    """Embed a single query string."""
    return (await embed_texts([text], model=model))[0]
