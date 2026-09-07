"""services — adapters for external systems, grouped by service *type*.

    services/
    ├── database/         postgres.py    (chat history + ingestion uploads)
    ├── message_broker/   kafka.py       (same adapter as ai-agents)
    ├── object_store/     minio.py       (raw CV/JD files, same adapter as ai-embeddings)
    └── observability/    langfuse.py    (read-side REST client for the admin page)

Rules (ai-agents/project_architecture.md §3): one folder per type, one module
per vendor, thin and stateless; no imports from core/handler/router;
configuration only via ``setting.settings``.
"""
