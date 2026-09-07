# ai-embeddings

Online ingestion service of resume-scan (FastAPI). It consumes upload events
from Kafka (`resume.uploaded`, `job.uploaded`), reads the raw file from MinIO,
runs the same extraction → structuring → chunking → embedding steps as the
offline `pre-processing/` notebooks, and upserts the vectors into Qdrant with
the exact same payload schema and deterministic point ids — so the chatbot's
retrieval keeps working unchanged. Progress and failures are reported back on
`resume.processed` / `job.processed` (bot-agent stores them, the admin portal
in `services/admin-portal` shows them), and every ingestion is one Langfuse
trace (`ingest-cv` / `ingest-jd`, tag `ingestion`).

The layering mirrors `ai-agents` (`router/ -> handler/ -> core/ -> services/`,
config via `setting.settings`; see `../ai-agents/project_architecture.md`).

## Pipeline

    Kafka resume.uploaded / job.uploaded   { upload_id, bucket, key, ... }
      -> MinIO get_object(bucket, key)
      -> core.extract      (PDF / DOCX / TXT / MD -> plain text; CVs get PII scrubbed)
      -> core.structure    (gpt-4.1 strict-JSON structuring, notebook schemas)
      -> core.chunking     (JD only: semantic chunks, SemanticChunker algorithm)
      -> core.points       (uuid5 ids + payloads, tiktoken truncation at 8000)
      -> core.embedding    (text-embedding-3-small, batches of 64 / 250k tokens)
      -> Qdrant            (ensure collection, delete stale points of the doc, upsert)
      -> Kafka resume.processed / job.processed   { status, points, error, trace_id }

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill OPENAI_API_KEY (+ Langfuse keys if you want traces)
python main.py             # http://localhost:8002/health?probe=true
```

Requires the `storage/` stack (Kafka, MinIO, Qdrant) — and `monitor/` for
Langfuse — to be up. In Docker the service is wired by
`services/docker-compose.yml`.

## Test

```bash
pip install -r requirements-dev.txt
pytest                     # points / chunking / structure helpers, no network
```

## Docker

```bash
cd .. && docker compose up -d --build ai-embeddings    # :8002, needs ai-embeddings/.env (OPENAI_API_KEY)
```

The image bakes the `cl100k_base` tokenizer so truncation works offline; the
compose entry injects the in-network addresses (`kafka:9092`, `minio:9000`,
`qdrant:6333`, `langfuse-web:3000`) over the host defaults of `.env`.

## Layout

```
ai-embeddings/
├── main.py              FastAPI app + Kafka consumer lifespan
├── router/ handler/     /health; handler/ingest.py = the Kafka use-case
├── core/                extract.py, structure.py, chunking.py, embedding.py, points.py, llm.py
├── prompt/              cv_extraction.md, jd_extraction.md
├── schemas/             ingest.py (Kafka contracts, mirrored in bot-agent/schemas/admin.py)
├── services/            message_broker/kafka, object_store/minio, vector_store/qdrant, observability/langfuse
├── setting/ utils/      Settings, logger
└── tests/
```
