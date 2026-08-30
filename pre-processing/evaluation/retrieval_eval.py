"""Layer-1 retrieval evaluation against the live Qdrant collections.

WHAT: embeds eval queries with the same model that indexed the collections,
runs grouped vector search (one group per resume/job id, mirroring the
production agent's ``query_points_groups`` usage in
``services/ai-agents``), and scores the ranked id lists with the pure metric
functions from :mod:`evaluation.retrieval_metrics` (MRR, accuracy@k,
precision@k, DCG/IDCG/NDCG@k). Also offers an optional LLM-graded NDCG pass
where the judge model assigns 0/1/2 relevance grades to the retrieved texts.

WHY: the synthetic datasets carry exactly one labelled relevant document per
query, so binary metrics come for free; the graded pass exists because other
retrieved documents may be genuinely relevant too, and binary NDCG would
undercount that.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import pandas as pd

from . import retrieval_metrics
from .config import (
    CV_COLLECTION,
    EMBED_MODEL,
    JD_COLLECTION,
    JUDGE_MODEL,
    get_openai,
    get_qdrant,
)

_NON_METRIC_COLUMNS = ("query", "relevant_id", "retrieved")


def _collection(kind: str) -> str:
    if kind == "cv":
        return CV_COLLECTION
    if kind == "jd":
        return JD_COLLECTION
    raise ValueError(f"kind must be 'cv' or 'jd', got {kind!r}")


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Embed ``texts`` with ``EMBED_MODEL`` in one call, order preserved."""
    response = get_openai().embeddings.create(model=EMBED_MODEL, input=list(texts))
    return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]


def search_ids(kind: str, vector: Sequence[float], k: int = 10) -> list:
    """Ranked document ids (best first) for one query vector.

    Grouped search collapses the many section/chunk points of one document
    into a single ranked group keyed by payload ``id`` — exactly how the
    production retrieval tools rank candidates/jobs. No ``type`` filter is
    applied for jd so both chunk and field points can match, same as prod.
    """
    result = get_qdrant().query_points_groups(
        collection_name=_collection(kind),
        group_by="id",
        query=list(vector),
        limit=k,
        group_size=1,
        with_payload=False,
    )
    return [group.id for group in result.groups]


def run_retrieval_eval(
    dataset: list[dict],
    k_values: Sequence[int] = (1, 3, 5, 10),
    search_k: int = 10,
) -> pd.DataFrame:
    """Score every query of a retrieval dataset; one row per query.

    Embeddings are batched into a single API call. Relevance is binary: the
    dataset's ``relevant_id`` has gain 1.0, everything else 0.0.
    """
    vectors = embed_texts([item["query"] for item in dataset])
    rows: list[dict[str, Any]] = []
    for item, vector in zip(dataset, vectors):
        retrieved = search_ids(item["kind"], vector, k=search_k)
        metrics = retrieval_metrics.evaluate_ranking(
            retrieved, {item["relevant_id"]: 1.0}, k_values=tuple(k_values)
        )
        rows.append(
            {
                "query": item["query"],
                "relevant_id": item["relevant_id"],
                "retrieved": retrieved,
                **metrics,
            }
        )
    return pd.DataFrame(rows)


def summarize_retrieval(df: pd.DataFrame) -> pd.DataFrame:
    """Single-row mean over the numeric metric columns, plus ``n_queries``."""
    metric_cols = [c for c in df.columns if c not in _NON_METRIC_COLUMNS]
    metric_df = df[metric_cols]
    # NaN (a query whose relevant doc never surfaced has rank=None) back to
    # None so the pure aggregator can skip it.
    records = metric_df.astype(object).where(pd.notna(metric_df), None)
    summary: dict[str, Any] = retrieval_metrics.aggregate(
        records.to_dict("records")
    )
    summary["n_queries"] = len(df)
    return pd.DataFrame([summary])


# --------------------------------------------------------------------------- #
# Optional deeper pass: LLM-graded NDCG
# --------------------------------------------------------------------------- #

_GRADE_SCHEMA = {
    "type": "object",
    "properties": {"grade": {"type": "integer", "enum": [0, 1, 2]}},
    "required": ["grade"],
    "additionalProperties": False,
}

_GRADE_SYSTEM = (
    "You grade the relevance of a retrieved document to a search query on a "
    "0-2 scale: 2 = the document clearly satisfies the query, 1 = partially "
    "relevant (some but not all key aspects match), 0 = not relevant. Judge "
    "only from the given text."
)


def judge_relevance(query: str, doc_text: str) -> int:
    """Graded relevance (0|1|2) of ``doc_text`` for ``query`` via the judge."""
    response = get_openai().chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": _GRADE_SYSTEM},
            {
                "role": "user",
                "content": f"Query: {query}\n\nDocument:\n{doc_text}",
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "relevance_grade",
                "strict": True,
                "schema": _GRADE_SCHEMA,
            },
        },
    )
    return int(json.loads(response.choices[0].message.content)["grade"])


def run_graded_ndcg(dataset: list[dict], k: int = 5) -> pd.DataFrame:
    """LLM-graded NDCG@k per query; one judged top-k list per dataset item.

    For each query the best point of each of the top-k groups is fetched with
    payload, its ``embedded_text`` is graded 0-2 by the judge, and DCG/IDCG/
    NDCG@k are computed with the judged grades as both the ranked gains and
    the ideal pool (the judged top-k is all we know about relevance here).
    """
    vectors = embed_texts([item["query"] for item in dataset])
    client = get_qdrant()
    rows: list[dict[str, Any]] = []
    for item, vector in zip(dataset, vectors):
        result = client.query_points_groups(
            collection_name=_collection(item["kind"]),
            group_by="id",
            query=list(vector),
            limit=k,
            group_size=1,
            with_payload=True,
        )
        retrieved: list = []
        grades: list[float] = []
        for group in result.groups:
            payload = group.hits[0].payload or {}
            text = str(payload.get("embedded_text", ""))
            retrieved.append(group.id)
            grades.append(float(judge_relevance(item["query"], text)))
        rows.append(
            {
                "query": item["query"],
                "relevant_id": item.get("relevant_id"),
                "retrieved": retrieved,
                "grades": grades,
                f"dcg@{k}": retrieval_metrics.dcg_at_k(grades, k),
                f"idcg@{k}": retrieval_metrics.idcg_at_k(grades, k),
                f"ndcg@{k}": retrieval_metrics.ndcg_at_k(grades, grades, k),
            }
        )
    return pd.DataFrame(rows)
