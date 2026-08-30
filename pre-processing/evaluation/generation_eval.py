"""Generation-layer evaluation: retrieve contexts, answer with the production
prompt, then score the answers with RAGAS.

WHAT: this module reproduces the production RAG answer path outside the agent
loop — embed the question, fetch grouped Qdrant hits shaped exactly like
``services/ai-agents/core/tools.py`` shapes them, and ask ``ANSWER_MODEL``
with the *exact* production system prompt (``config.SYSTEM_PROMPT_PATH``).
The resulting (question, contexts, answer, ground_truth) samples are scored
with RAGAS judge-based metrics (faithfulness, answer correctness, answer
relevancy, context precision/recall) and summarised into a hallucination
report.

WHY: retrieval metrics alone cannot tell whether the *generated* answer is
grounded in what was retrieved. Reusing the production prompt and the
production context shaping means the RAGAS scores measure the system users
actually get, not a simplified stand-in.

RAGAS note: written against ragas 0.4.x (``EvaluationDataset`` /
``SingleTurnSample`` + ``evaluate`` with ``LangchainLLMWrapper`` /
``LangchainEmbeddingsWrapper``); the imports live inside :func:`run_ragas`
because ragas is a heavy dependency that only that function needs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Sequence

import pandas as pd

from . import config

try:  # shared embedding helper from the retrieval layer …
    from .retrieval_eval import embed_texts
except ImportError:  # pragma: no cover — … local fallback so the module stands alone

    def embed_texts(texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` with ``EMBED_MODEL``, preserving order."""
        response = config.get_openai().embeddings.create(
            model=config.EMBED_MODEL, input=list(texts)
        )
        return [
            item.embedding
            for item in sorted(response.data, key=lambda d: d.index)
        ]


log = logging.getLogger(__name__)

#: Longest snippet forwarded per matching section/chunk — mirrors
#: ``_SNIPPET_CHARS`` in services/ai-agents/core/tools.py.
_SNIPPET_CHARS = 600

_COLLECTIONS = {"cv": config.CV_COLLECTION, "jd": config.JD_COLLECTION}


def _snippet(text: Any) -> str:
    """Trim ``text`` like the production tool layer does (600 chars + ellipsis)."""
    text = str(text or "").strip()
    return text[:_SNIPPET_CHARS] + ("…" if len(text) > _SNIPPET_CHARS else "")


def retrieve_contexts(kind: str, query: str, k: int = 5) -> list[str]:
    """Return ``k`` context strings for ``query`` — one per resume/job group.

    ``kind`` is ``"cv"`` or ``"jd"``. Hits are grouped by the shared payload
    ``id`` (group_size=3) and each group is flattened into one string shaped
    like the production tool output: ``resume #<id> [<field>]: <snippet>`` for
    CVs; ``job #<id>`` plus job_title/company (when a ``type=field`` payload is
    in the group) and the matched snippets for JDs. No type filter is applied
    for JDs so chunk and field points both match, same as production.
    """
    collection = _COLLECTIONS[kind]
    vector = embed_texts([query])[0]
    groups = config.get_qdrant().query_points_groups(
        collection_name=collection,
        query=vector,
        group_by="id",
        limit=k,
        group_size=3,
        with_payload=True,
    ).groups

    contexts: list[str] = []
    for group in groups:
        payloads = [hit.payload or {} for hit in group.hits]
        if kind == "cv":
            sections = " ".join(
                f"[{p.get('field')}]: {_snippet(p.get('embedded_text'))}" for p in payloads
            )
            contexts.append(f"resume #{group.id} {sections}")
        else:
            header = f"job #{group.id}"
            record = next((p for p in payloads if p.get("type") == "field"), None)
            if record is not None:
                label = " at ".join(
                    part
                    for part in (
                        str(record.get("job_title") or "").strip(),
                        str(record.get("company") or "").strip(),
                    )
                    if part
                )
                if label:
                    header = f"{header} — {label}"
            snippets = " | ".join(_snippet(p.get("embedded_text")) for p in payloads)
            contexts.append(f"{header}: {snippets}")
    log.debug("retrieve_contexts(%s, %r) -> %d contexts", kind, query, len(contexts))
    return contexts


def generate_answer(question: str, contexts: list[str]) -> str:
    """Answer ``question`` from ``contexts`` using the production system prompt.

    The contexts are presented in a single user message (numbered, prefixed
    with ``Retrieved results:``) followed by the question — a flattened stand-in
    for the tool-message transcript the production agent sees.
    """
    numbered = "\n".join(f"{i}. {context}" for i, context in enumerate(contexts, 1))
    response = config.get_openai().chat.completions.create(
        model=config.ANSWER_MODEL,
        messages=[
            {"role": "system", "content": config.SYSTEM_PROMPT_PATH.read_text()},
            {
                "role": "user",
                "content": f"Retrieved results:\n{numbered}\n\nQuestion: {question}",
            },
        ],
    )
    return (response.choices[0].message.content or "").strip()


def run_generation(dataset: list[dict], k: int = 5, force: bool = False) -> list[dict]:
    """Run retrieve+generate for every QA item; cache the result under EVAL_DIR.

    Each returned sample carries ``question``, ``ground_truth``, ``contexts``,
    ``answer``, ``source_id`` and ``kind``. The cache
    (``gen_<kind>_<n>_<k>_<hash>.json``, hash over the question list so
    datasets with different seeds never collide on one file) is reused only
    when its questions still match the dataset, and can be bypassed with
    ``force=True`` — generation calls the answer model once per item, which
    costs real money.
    """
    if not dataset:
        return []
    kind = str(dataset[0].get("kind", "cv"))
    questions = [item["question"] for item in dataset]
    digest = hashlib.sha256(
        "\n".join(questions).encode("utf-8")
    ).hexdigest()[:10]
    cache_path = config.EVAL_DIR / f"gen_{kind}_{len(dataset)}_{k}_{digest}.json"
    if cache_path.exists() and not force:
        cached = json.loads(cache_path.read_text())
        if [sample.get("question") for sample in cached] == questions:
            log.info("run_generation: reusing cache %s", cache_path.name)
            return cached
        log.info("run_generation: cache %s stale, regenerating", cache_path.name)

    samples: list[dict] = []
    for item in dataset:
        item_kind = str(item.get("kind", kind))
        contexts = retrieve_contexts(item_kind, item["question"], k=k)
        samples.append(
            {
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "contexts": contexts,
                "answer": generate_answer(item["question"], contexts),
                "source_id": item.get("source_id"),
                "kind": item_kind,
            }
        )
    cache_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
    return samples


def run_ragas(samples: list[dict]) -> pd.DataFrame:
    """Score generation samples with RAGAS; one row per sample.

    Uses the ragas 0.4.x API: ``SingleTurnSample(user_input, retrieved_contexts,
    response, reference)`` inside an ``EvaluationDataset``, judged by
    ``JUDGE_MODEL`` (via ``LangchainLLMWrapper(ChatOpenAI)``) with
    ``EMBED_MODEL`` embeddings. Metrics: ``Faithfulness``,
    ``AnswerCorrectness``, ``ResponseRelevancy`` (column ``answer_relevancy``),
    ``LLMContextPrecisionWithReference`` and ``LLMContextRecall`` (column
    ``context_recall``). The returned frame adds plain ``question`` /
    ``answer`` / ``ground_truth`` columns next to ragas's own column names.
    """
    # Heavy imports kept local: everything else in this module works without ragas.
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        AnswerCorrectness,
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    judge = LangchainLLMWrapper(
        ChatOpenAI(model=config.JUDGE_MODEL, api_key=config.OPENAI_API_KEY, temperature=0)
    )
    embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(model=config.EMBED_MODEL, api_key=config.OPENAI_API_KEY)
    )
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=sample["question"],
                retrieved_contexts=list(sample["contexts"]),
                response=sample["answer"],
                reference=sample["ground_truth"],
            )
            for sample in samples
        ]
    )
    result = evaluate(
        dataset=dataset,
        metrics=[
            Faithfulness(),
            AnswerCorrectness(),
            ResponseRelevancy(),
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
        ],
        llm=judge,
        embeddings=embeddings,
        show_progress=False,
    )
    df = result.to_pandas()
    df["question"] = [sample["question"] for sample in samples]
    df["answer"] = [sample["answer"] for sample in samples]
    df["ground_truth"] = [sample["ground_truth"] for sample in samples]
    return df


def _numeric_column(df: pd.DataFrame, name: str) -> pd.Series:
    """Numeric view of ``df[name]``; an all-NaN series if the column is absent."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index, dtype=float)


def hallucination_report(df: pd.DataFrame, threshold: float = 0.7) -> dict:
    """Summarise faithfulness: an answer scoring below ``threshold`` is flagged
    as (potentially) hallucinated.

    ``hallucination_rate`` is the share of samples with faithfulness <
    ``threshold`` — strictly per the metric definition, so a NaN score (the
    ragas judge failed to produce one) does NOT count as hallucinated; such
    unverifiable samples are counted separately in ``n_unscored`` and still
    listed in ``flagged`` for inspection. Returns ``n``, ``n_unscored``,
    ``hallucination_rate``, ``mean_faithfulness``,
    ``mean_answer_correctness`` and ``flagged`` — a DataFrame of
    question/answer/faithfulness rows, least faithful first (NaN first).
    """
    n = int(len(df))
    faithfulness = _numeric_column(df, "faithfulness")
    hallucinated_mask = faithfulness < threshold  # NaN compares False
    unscored_mask = faithfulness.isna()
    review_mask = hallucinated_mask | unscored_mask
    flag_cols = [c for c in ("question", "answer") if c in df.columns]
    flagged = df.loc[review_mask, flag_cols].copy()
    flagged["faithfulness"] = faithfulness[review_mask]
    flagged = flagged.sort_values(
        "faithfulness", ascending=True, na_position="first"
    ).reset_index(drop=True)
    correctness = _numeric_column(df, "answer_correctness")
    return {
        "n": n,
        "n_unscored": int(unscored_mask.sum()),
        "hallucination_rate": float(hallucinated_mask.sum() / n) if n else 0.0,
        "mean_faithfulness": float(faithfulness.mean()) if n else float("nan"),
        "mean_answer_correctness": float(correctness.mean()) if n else float("nan"),
        "flagged": flagged,
    }
