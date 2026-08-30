"""Two-layer evaluation pipeline for the resume/job RAG system.

Layer 1 — retrieval quality (``retrieval_metrics``, ``retrieval_eval``):
    Given LLM-generated recruiter-style queries with a known relevant
    document, score the ranked lists returned by Qdrant with rank-aware
    metrics: NDCG/DCG/IDCG (graded-gain, log2 discount), MRR (reciprocal
    rank of the first relevant hit), accuracy@k (hit rate in the top k),
    and precision@k.

Layer 2 — generation quality (``generation_eval``):
    Run the production system prompt over retrieved contexts, then score
    the answers with RAGAS: faithfulness (grounding in the contexts —
    low faithfulness is flagged as hallucination), answer correctness
    against the ground truth, answer relevancy, and context
    precision/recall.

Modules are intentionally NOT re-exported here: each submodule pulls its
own heavy dependencies (openai, qdrant-client, ragas), and keeping this
package init empty lets the pure-metric layer import and test cleanly on
partial installs.
"""
