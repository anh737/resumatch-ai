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

## API

| Endpoint | Description |
|---|---|
| `POST /chat` | `{conversation_id?, message}` → `202 {conversation_id, message_id, status:"queued"}` |
| `GET /conversations` | Conversations, most recently active first |
| `GET /conversations/{id}/messages` | Full message history (incl. `tool_calls`, `suggestions`, `error`) |
| `DELETE /conversations/{id}` | Remove a conversation and its messages |
| `GET /health?probe=true` | Liveness; `probe` also pings PostgreSQL and Kafka |

## Folder layout

Same layering as `ai-agents` (see `../ai-agents/project_architecture.md` §3):
`router/ -> handler/ -> services/`, config via `setting.settings`, prompts none.
`services/message_broker/kafka.py` is the shared Kafka adapter;
`services/database/postgres.py` owns the `conversations` / `chat_messages`
tables (created automatically at startup).

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
pytest                    # PostgreSQL/Kafka tests run live against the storage stack (auto-skip when down)
```

## Docker

Built and started together with the other application services:

```bash
cd .. && docker compose up -d --build    # bot-agent listens on :8001
```

The compose entry overrides `POSTGRES_URL` / `KAFKA_BOOTSTRAP_SERVERS` with
the in-network addresses (`postgres:5432`, `kafka:9092`).
