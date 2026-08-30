"""services — adapters for external systems, grouped by service *type*.

    services/
    ├── database/         postgres.py    (chat history persistence)
    └── message_broker/   kafka.py       (same adapter as ai-agents)

Rules (ai-agents/project_architecture.md §3): one folder per type, one module
per vendor, thin and stateless; no imports from core/handler/router;
configuration only via ``setting.settings``.
"""
