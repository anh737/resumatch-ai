"""Central configuration for the evaluation package.

WHAT: filesystem paths, model names, Qdrant collection names, and lazily
cached OpenAI / Qdrant clients shared by every evaluation module.

WHY: the retrieval and generation evaluations must talk to exactly the same
backends (embedding model, collections, system prompt) as the production RAG
agent; pinning those choices in one module keeps the notebook, the dataset
builders, and the metric runners consistent and makes the whole pipeline
configurable from a single place.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import openai
import qdrant_client
from dotenv import load_dotenv

#: pre-processing/ directory — the package lives in pre-processing/evaluation/.
ROOT: Path = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
QDRANT_URL: str = "http://localhost:6333"
QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")

# Same embedding model that indexed both collections; answers come from the
# production-grade model while a cheaper model serves as generator/judge for
# eval datasets and graded relevance.
EMBED_MODEL: str = "text-embedding-3-small"
ANSWER_MODEL: str = "gpt-4.1"
JUDGE_MODEL: str = "gpt-4.1-mini"

CV_COLLECTION: str = "cv_information_technology"
JD_COLLECTION: str = "jd_jobs"

#: Cached eval datasets (LLM-generated, keyed by kind/n/seed) live here …
EVAL_DIR: Path = ROOT / "eval_data"
#: … and computed metric tables / figures here.
RESULTS_DIR: Path = EVAL_DIR / "results"
EVAL_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

RECORDS_PATH: Path = ROOT / "output" / "records.json"
JOBS_PATH: Path = ROOT / "output" / "jobs.json"

#: The production answer prompt — generation eval must reuse this exact file.
SYSTEM_PROMPT_PATH: Path = Path(
    "/Users/anh/Doc/resume-scan-project/services/ai-agents/prompt/system.md"
)


@lru_cache(maxsize=1)
def get_openai() -> openai.OpenAI:
    """Return the process-wide OpenAI client (created once, then cached)."""
    return openai.OpenAI(api_key=OPENAI_API_KEY)


@lru_cache(maxsize=1)
def get_qdrant() -> qdrant_client.QdrantClient:
    """Return the process-wide synchronous Qdrant client (cached)."""
    return qdrant_client.QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
