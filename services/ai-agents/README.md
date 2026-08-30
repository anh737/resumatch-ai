# ai-agents

Service AI agent cho resume-scan (FastAPI + OpenAI).

## Folder layout

```
ai-agents/
├── main.py            # entrypoint FastAPI
├── core/              # cấu hình ai-agent (LLM client, agent definition, tools)
├── handler/           # logic xử lý (router -> handler -> services/core)
├── prompt/            # file prompting (system prompt, template)
├── router/            # khai báo API routes, chỉ map endpoint -> handler
├── schemas/           # kiểu dữ liệu request/response (pydantic)
├── services/          # adapter cho service ngoài, gom theo LOẠI: vector_store/qdrant.py, message_broker/kafka.py, observability/langfuse.py
├── setting/           # config app, đọc từ .env
└── utils/             # function dùng chung (logger, helpers)
```

## Architecture

Xem [project_architecture.md](project_architecture.md): system context, deployment
topology (`storage-net`), layering rules, chat contracts, config
host-vs-container, và roadmap.

Xem [ai_processing.md](ai_processing.md): cách xử lý AI — 3 stage của agent
(tool loop → summary → suggestions), retrieval, prompting, memory, Langfuse
tracing, failure handling.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # điền OPENAI_API_KEY; LANGFUSE_PUBLIC_KEY/SECRET_KEY lấy từ Langfuse UI (project "resume")
python main.py            # http://localhost:8000/health
```

## Test

```bash
pip install -r requirements-dev.txt
pytest                    # Qdrant/Kafka test chạy live với storage stack (tự skip nếu stack chưa lên)
```
