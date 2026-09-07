# bot-agent

Chat-history service for resume-scan (FastAPI + PostgreSQL + Kafka). It is the
front-end's HTTP entry point for chat:

1. `POST /chat` stores the user message in PostgreSQL, loads the last
   `CHAT_HISTORY_TURNS` (default 10) user+assistant pairs, and forwards
   everything to `ai-agents` on the Kafka topic `chat.requests`.
2. `ai-agents` answers the browser directly over its WebSocket
   (`/ws/chat/{conversation_id}`) and publishes the finished turn on
   `chat.responses`.
3. bot-agent consumes `chat.responses` and persists the assistant answer plus
   the tool calls the agent executed and the follow-up suggestions it proposed.

It is also the backend of the **admin portal** (`services/admin-portal`):
`POST /admin/uploads` stores a CV/JD file in MinIO, records it in the
`ingestion_uploads` table and produces `resume.uploaded` / `job.uploaded`
(key = upload id); `ai-embeddings` ingests the file and reports back on
`resume.processed` / `job.processed`, which bot-agent consumes to update the
row's status. `GET /admin/traces` and `GET /admin/traces/{id}` proxy the
Langfuse public API (read-only) so the portal can list traces and open an
observation tree without exposing the keys.

## API

| Endpoint | Description |
|---|---|
| `POST /chat` | `{conversation_id?, message}` → `202 {conversation_id, message_id, status:"queued"}` |
| `GET /conversations` | Conversations, most recently active first |
| `GET /conversations/{id}/messages` | Full message history (incl. `tool_calls`, `suggestions`, `error`) |
| `DELETE /conversations/{id}` | Remove a conversation and its messages |
| `GET /health?probe=true` | Liveness; `probe` also pings PostgreSQL, Kafka, MinIO and Langfuse |
| `POST /admin/uploads` | multipart `{kind: cv\|jd, file, category?}` → `202` upload row; stores in MinIO + produces the upload event |
| `GET /admin/uploads?kind&status&limit` | Ingestion rows, newest first (`queued → processing → done/failed`), optional filters |
| `GET /admin/uploads/stats` | Counters per kind / status + points upserted (overview page) |
| `GET /admin/uploads/{id}` | One ingestion row |
| `POST /admin/uploads/{id}/reprocess` | Re-run the ingestion of a file already in MinIO (row back to `queued`, event re-produced) |
| `GET /admin/traces?limit&page&tags&name` | Recent Langfuse traces (`{configured, public_url, items[], page, total, total_pages, error}`) |
| `GET /admin/traces/{trace_id}` | One trace with its observations (type, model, tokens, cost, level, I/O) and scores |

## Folder layout

Same layering as `ai-agents` (see `../ai-agents/project_architecture.md` §3):
`router/ -> handler/ -> services/`, config via `setting.settings`, prompts none.
`services/message_broker/kafka.py` is the shared Kafka adapter;
`services/database/postgres.py` owns the `conversations` / `chat_messages` /
`ingestion_uploads` tables (created automatically at startup);
`services/object_store/minio.py` stores the raw uploads;
`services/observability/langfuse.py` is the read-only Langfuse REST client.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py            # http://localhost:8001/health
```

## Test

```bash
pip install -r requirements-dev.txt
pytest                    # admin/chat handlers + router wiring with fakes; the PostgreSQL round trip runs live (auto-skips when down)
```

## Docker

Built and started together with the other application services:

```bash
cd .. && docker compose up -d --build    # bot-agent listens on :8001
```

The compose entry overrides `POSTGRES_URL` / `KAFKA_BOOTSTRAP_SERVERS` /
`MINIO_*` / `LANGFUSE_HOST` with the in-network addresses (`postgres:5432`,
`kafka:9092`, `minio:9000`, `langfuse-web:3000`); the Langfuse keys come from
`bot-agent/.env`.
