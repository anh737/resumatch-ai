"""Pure ranking metrics for retrieval evaluation (layer 1).

WHAT: rank-aware scoring of a retrieved, best-first list of document ids
against a set (or graded map) of relevant ids — reciprocal rank / MRR,
hit ("accuracy") and precision at k, and DCG/IDCG/NDCG with graded gains.

WHY a separate module: these functions are pure (no I/O, no network, no
numpy), so they can be unit-tested with hand-computed values and reused
by both the retrieval evaluation runner and the graded-NDCG judge pass.

DCG convention used throughout: for a best-first list of gains
``g_1, g_2, ...`` (1-based position ``i``),

    DCG@k = sum_{i=1..k} g_i / log2(i + 1)

so position 1 is discounted by log2(2) = 1 (no discount), position 2 by
log2(3), etc. IDCG@k is the DCG@k of the gains sorted descending, and
NDCG@k = DCG@k / IDCG@k (0.0 when IDCG is 0).

For the single-ground-truth datasets used here (exactly one relevant id
with gain 1.0), IDCG@k == 1 for every k >= 1, so

    NDCG@k == 1 / log2(rank + 1)   if the relevant doc is at rank <= k,
    NDCG@k == 0                    otherwise.
"""

from __future__ import annotations

import math
from typing import Any, Collection, Mapping, Sequence

__all__ = [
    "reciprocal_rank",
    "hit_at_k",
    "precision_at_k",
    "dcg_at_k",
    "idcg_at_k",
    "ndcg_at_k",
    "evaluate_ranking",
    "aggregate",
]


def reciprocal_rank(ranked_ids: Sequence, relevant_ids: Collection) -> float:
    """Return 1/rank of the first relevant id in ``ranked_ids`` (1-based).

    Returns 0.0 when no relevant id appears in the ranking.
    """
    for position, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / position
    return 0.0


def hit_at_k(ranked_ids: Sequence, relevant_ids: Collection, k: int) -> float:
    """Return 1.0 if any relevant id appears in the top ``k``, else 0.0.

    This is the "accuracy@k" metric for single-ground-truth datasets.
    """
    if k <= 0:
        return 0.0
    return 1.0 if any(doc_id in relevant_ids for doc_id in ranked_ids[:k]) else 0.0


def precision_at_k(ranked_ids: Sequence, relevant_ids: Collection, k: int) -> float:
    """Return the fraction of the top ``k`` positions holding a relevant id.

    The denominator is ``k`` even when fewer than ``k`` ids were retrieved
    (the standard definition: missing positions count as misses).
    """
    if k <= 0:
        return 0.0
    hits = sum(1 for doc_id in ranked_ids[:k] if doc_id in relevant_ids)
    return hits / k


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    """Discounted cumulative gain of a best-first gain list, truncated at ``k``.

    ``DCG@k = sum_{i=1..k} gains[i-1] / log2(i + 1)`` with 1-based ``i``.
    Lists shorter than ``k`` simply contribute their available positions.
    """
    if k <= 0:
        return 0.0
    return sum(
        (
            gain / math.log2(position + 1)
            for position, gain in enumerate(gains[:k], start=1)
        ),
        0.0,
    )


def idcg_at_k(all_gains: Sequence[float], k: int) -> float:
    """Ideal DCG@k: the DCG of ``all_gains`` sorted in descending order."""
    return dcg_at_k(sorted(all_gains, reverse=True), k)


def ndcg_at_k(
    ranked_gains: Sequence[float], all_gains: Sequence[float], k: int
) -> float:
    """Normalized DCG@k: ``dcg_at_k(ranked_gains, k) / idcg_at_k(all_gains, k)``.

    Returns 0.0 when the ideal DCG is 0 (no positive gain exists at all).
    """
    ideal = idcg_at_k(all_gains, k)
    if ideal == 0.0:
        return 0.0
    return dcg_at_k(ranked_gains, k) / ideal


def evaluate_ranking(
    ranked_ids: Sequence,
    relevance: Mapping[Any, float],
    k_values: Sequence[int] = (1, 3, 5, 10),
) -> dict:
    """Score one ranked list against a graded relevance map.

    ``relevance`` maps ids to gains; ids with gain > 0 count as relevant
    for rank/MRR/accuracy/precision. Gains for the ranked list default to
    0.0 for unknown ids; ``all_gains`` (the IDCG basis) is every gain in
    ``relevance``.

    Returns a flat dict: ``rank`` (1-based rank of the first relevant id,
    or None if absent), ``mrr`` (its reciprocal, 0.0 if absent), and for
    each k in ``k_values`` the keys ``accuracy@{k}``, ``precision@{k}``,
    ``dcg@{k}``, ``idcg@{k}``, ``ndcg@{k}``.

    Duplicate ids in ``ranked_ids`` are collapsed to their first (best)
    occurrence before scoring: a repeated document must not earn its gain
    twice, otherwise NDCG could exceed 1 and precision would double-count
    hits. (Grouped Qdrant search never yields duplicates, so this is pure
    robustness.)
    """
    seen: set = set()
    deduped = []
    for doc_id in ranked_ids:
        if doc_id not in seen:
            seen.add(doc_id)
            deduped.append(doc_id)
    ranked_ids = deduped

    relevant_ids = {doc_id for doc_id, gain in relevance.items() if gain > 0}

    rank: int | None = None
    for position, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant_ids:
            rank = position
            break

    ranked_gains = [float(relevance.get(doc_id, 0.0)) for doc_id in ranked_ids]
    all_gains = [float(gain) for gain in relevance.values()]

    result: dict = {
        "rank": rank,
        "mrr": reciprocal_rank(ranked_ids, relevant_ids),
    }
    for k in k_values:
        result[f"accuracy@{k}"] = hit_at_k(ranked_ids, relevant_ids, k)
        result[f"precision@{k}"] = precision_at_k(ranked_ids, relevant_ids, k)
        result[f"dcg@{k}"] = dcg_at_k(ranked_gains, k)
        result[f"idcg@{k}"] = idcg_at_k(all_gains, k)
        result[f"ndcg@{k}"] = ndcg_at_k(ranked_gains, all_gains, k)
    return result


def aggregate(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """Mean of every numeric key across ``rows``, ignoring None values.

    Each key is averaged over the rows where it holds an int/float
    (bools and non-numeric values are skipped, as are None entries — so a
    ``rank`` of None simply drops out of that key's mean).
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            sums[key] = sums.get(key, 0.0) + float(value)
            counts[key] = counts.get(key, 0) + 1
    return {key: sums[key] / counts[key] for key in sums}
