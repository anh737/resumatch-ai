# `evaluation/` — Resume-Scan chatbot evaluation package

A two-layer evaluation pipeline for the resume/job RAG chatbot, driven by the notebook
`pre-processing/evaluation_system.ipynb`:

- **Layer 1 — Retrieval quality.** LLM-generated recruiter/job-seeker queries with a known
  relevant document are searched against the live Qdrant collections and scored with
  rank-aware metrics (MRR, accuracy@k, precision@k, DCG/IDCG/NDCG@k), plus an optional
  LLM-graded NDCG pass.
- **Layer 2 — Generation quality / hallucination.** Single-document QA pairs are answered by
  the production pipeline (the exact production system prompt from
  `services/ai-agents/prompt/system.md` and the same grouped-context shaping as
  `services/ai-agents/core/tools.py`), then scored with RAGAS — faithfulness (grounding in
  the retrieved contexts; low = hallucination), answer correctness (vs. ground truth), answer
  relevancy, and context precision/recall — and summarised into a hallucination report.

## Layout

```
pre-processing/
├── evaluation_system.ipynb     # the orchestration/report notebook (run this)
├── requirements-eval.txt       # deps for the dedicated .venv-eval environment
├── eval_data/                  # cached LLM-generated datasets + generations
│   └── results/                # timestamped CSV/JSON outputs of each run
└── evaluation/
    ├── __init__.py             # package docstring (no re-exports)
    ├── config.py               # paths, model & collection names, cached clients
    ├── dataset.py              # deterministic sampling + LLM dataset builders (cached)
    ├── retrieval_metrics.py    # pure ranking metrics (no I/O — unit tested)
    ├── retrieval_eval.py       # layer 1: embed → grouped Qdrant search → score
    ├── generation_eval.py      # layer 2: retrieve → answer → RAGAS → report
    └── tests/
        └── test_metrics.py     # hand-computed tests for retrieval_metrics
```

## Setup: environment, kernel, running the notebook

```bash
cd pre-processing

# 1. dedicated venv (idempotent)
python3.11 -m venv .venv-eval
./.venv-eval/bin/pip install -r requirements-eval.txt

# 2. secrets: pre-processing/.env must define OPENAI_API_KEY and QDRANT_API_KEY
#    (Qdrant itself must be running at http://localhost:6333)

# 3. sanity: pure-metric unit tests (no network, no keys needed)
./.venv-eval/bin/python -m pytest evaluation/tests -q

# 4. optional: register the venv as a Jupyter kernel for interactive use
./.venv-eval/bin/python -m ipykernel install --user \
    --name resume-eval --display-name "Python 3 (.venv-eval)"

# 5. execute the notebook headlessly (or open it in Jupyter with that kernel)
./.venv-eval/bin/python - <<'PY'
import nbformat
from nbclient import NotebookClient
nb = nbformat.read("evaluation_system.ipynb", as_version=4)
NotebookClient(nb, timeout=1800).execute()
nbformat.write(nb, "evaluation_system.ipynb")
PY
```

Results land in `eval_data/results/` as timestamped files: per-query retrieval CSVs (cv and
jd), a retrieval summary CSV (cv / jd / combined means), a per-sample RAGAS CSV, and a
hallucination-report JSON (parameters + summary numbers + flagged answers).

## Module map

| Module | What it does |
|---|---|
| `config.py` | Single source of truth: `ROOT`, `.env` loading, model names (`EMBED_MODEL=text-embedding-3-small`, `ANSWER_MODEL=gpt-4.1`, `JUDGE_MODEL=gpt-4.1-mini`), collection names, `EVAL_DIR`/`RESULTS_DIR`, and `lru_cache`'d `get_openai()` / `get_qdrant()` clients. |
| `dataset.py` | `sample_resumes` / `sample_jobs` (seeded, deterministic over id-sorted corpora); `build_retrieval_dataset(kind, n, seed)` → `{query, relevant_id, kind}`; `build_qa_dataset(kind, n, seed)` → `{question, ground_truth, source_id, kind}`. Queries/QA are written by the judge model via strict JSON-schema structured output, grounded only in the one document (ids are never shown to the LLM). |
| `retrieval_metrics.py` | Pure functions: `reciprocal_rank`, `hit_at_k` (accuracy@k), `precision_at_k`, `dcg_at_k`, `idcg_at_k`, `ndcg_at_k`, `evaluate_ranking`, `aggregate`. No I/O, no numpy — fully unit tested. |
| `retrieval_eval.py` | `embed_texts` (one batched OpenAI call), `search_ids` (grouped Qdrant search, one group per document id — mirrors production), `run_retrieval_eval` → per-query DataFrame, `summarize_retrieval` → mean row, `judge_relevance` (0/1/2 grade) and `run_graded_ndcg` for the graded pass. |
| `generation_eval.py` | `retrieve_contexts` (production-shaped context strings), `generate_answer` (production system prompt + `gpt-4.1`), `run_generation` (cached), `run_ragas` (ragas 0.4.x `EvaluationDataset`/`SingleTurnSample` API), `hallucination_report`. |

## Metric definitions

Let a query's ranked, best-first result list have gains `g_1, g_2, …` (position `i` is
1-based). In the standard layer-1 setup each query has exactly one relevant document with gain
1.0; every other document has gain 0.

- **rank** — position of the first relevant document (`None` if absent from the top
  `SEARCH_K`).
- **MRR** — mean over queries of `1/rank` (0 when absent).
- **accuracy@k** (hit rate) — 1 if any relevant document is in the top `k`, else 0; averaged
  over queries.
- **precision@k** — (# relevant in top `k`) / `k`. With one relevant document it is capped at
  `1/k`.
- **DCG@k** — discounted cumulative gain:

  ```
  DCG@k  = Σ_{i=1..k}  g_i / log2(i + 1)
  ```

- **IDCG@k** — ideal DCG: the DCG@k of all gains sorted descending (the best achievable
  ordering):

  ```
  IDCG@k = Σ_{i=1..k}  g*_i / log2(i + 1)      with g*_1 ≥ g*_2 ≥ …
  ```

- **NDCG@k** = `DCG@k / IDCG@k` (defined as 0 when IDCG@k = 0). With a single gain-1.0
  document, IDCG@k = 1 for every k ≥ 1, so NDCG@k reduces to `1/log2(rank+1)` if `rank ≤ k`,
  else 0. IDCG only becomes non-trivial in the **LLM-graded pass**, where the judge assigns
  0–2 grades to every retrieved document and the ideal ordering actually differs from a single
  spike.

Layer 2 (RAGAS, judged by `JUDGE_MODEL`):

- **faithfulness** — share of claims in the answer supported by the retrieved contexts.
  **Hallucination is defined as `faithfulness < HALLUCINATION_THRESHOLD` (default 0.7)**; a
  `NaN` score counts as flagged (unverifiable).
- **answer_correctness** — agreement of the answer with the ground-truth answer (factual +
  semantic).
- **answer_relevancy** — does the answer actually address the question.
- **context precision (with reference) / context recall** — was the needed evidence retrieved
  and ranked high; attributes low downstream scores to the retrieval step.

## Cost notes

Everything billable is an OpenAI call; Qdrant search is free/local. With the notebook defaults
(`RETRIEVAL_N=40`, `QA_N=15`, `GRADED_N=8`, `CONTEXT_K=5`):

- **Dataset builders** (once per `kind`/`n`/`seed`, then cached): ~80 query generations +
  ~30 QA generations on `gpt-4.1-mini` — cents.
- **Layer 1 runs**: 2 batched embedding calls (~80 short queries on
  `text-embedding-3-small`) — negligible; re-running is essentially free.
- **Graded NDCG**: `GRADED_N × 5 = 40` judge calls — cents.
- **Layer 2 generation**: ~30 `gpt-4.1` completions with ~5 contexts each — the priciest
  step (order of a dollar); cached under `eval_data/gen_*.json` after the first run.
- **RAGAS**: ~5 judged metrics × 30 samples on `gpt-4.1-mini`, several calls per metric —
  tens of cents. Not cached — re-runs re-judge, which also means scores jitter slightly
  between runs (LLM-judge variance).

## Caching — how it works

All caches are plain JSON under `pre-processing/eval_data/`:

- `retrieval_{kind}_{n}_{seed}.json` — retrieval query datasets.
- `qa_{kind}_{n}_{seed}.json` — QA datasets.
- `gen_{kind}_{n}_{k}.json` — generated answers + contexts; reused only if its questions
  still match the requested dataset, so a changed QA cache invalidates it automatically.

Builders load the cache when the file exists; pass `force=True` (the notebook's
`FORCE_REBUILD` parameter) to regenerate and overwrite. Because sampling is seeded over
id-sorted corpora, the same `n`/`seed` always selects the same documents — comparisons across
configurations are apples-to-apples as long as the cache files are kept. Delete a file (or
change `n`/`seed`) to get a fresh dataset; `eval_data/results/` outputs are timestamped and
never overwritten.
