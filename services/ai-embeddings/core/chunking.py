"""Semantic chunking of job descriptions — port of the offline pipeline's chunker.

``pre-processing/preprocess_jd.ipynb`` uses ``langchain_experimental``'s
``SemanticChunker`` with its defaults; this module reimplements that exact
algorithm directly on the OpenAI SDK (the house rule: no agent frameworks) so
the service does not drag the langchain stack in:

1. split the text into sentences on ``(?<=[.?!])\\s+``;
2. combine each sentence with ``CHUNK_BUFFER_SIZE`` neighbors per side and
   embed the combined windows;
3. compute cosine distances between consecutive windows;
4. break wherever the distance exceeds the ``CHUNK_PERCENTILE`` (95th)
   percentile of all distances, and join each run of sentences with spaces.

Chunk boundaries feed the deterministic point ids
(``jd_chunk:{job_id}:{index}``), so all stale points of a job are deleted
before its fresh chunks are upserted (see ``handler.ingest``).
"""

from __future__ import annotations

import math
import re

from core.llm import embed_texts
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

_SENTENCE_RE = re.compile(r"(?<=[.?!])\s+")


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - (dot / norm if norm else 0.0)


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy.percentile's default method)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


async def semantic_chunks(text: str) -> list[str]:
    """Split ``text`` into semantically coherent chunks (order preserved)."""
    text = text.strip()
    if not text:
        return []
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        return [text]

    buffer = settings.CHUNK_BUFFER_SIZE
    windows = [
        " ".join(sentences[max(0, i - buffer) : i + buffer + 1])
        for i in range(len(sentences))
    ]
    vectors = await embed_texts(windows)
    distances = [_cosine_distance(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
    threshold = _percentile(distances, settings.CHUNK_PERCENTILE)

    chunks: list[str] = []
    start = 0
    for i, distance in enumerate(distances):
        if distance > threshold:
            chunks.append(" ".join(sentences[start : i + 1]))
            start = i + 1
    chunks.append(" ".join(sentences[start:]))
    chunks = [c for c in chunks if c.strip()]
    log.debug("chunked %d sentences into %d chunks", len(sentences), len(chunks))
    return chunks
