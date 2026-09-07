"""services — adapters for external systems, grouped by service *type*.

    services/
    ├── vector_store/     qdrant.py      (future: chromadb.py, …)
    ├── message_broker/   kafka.py       (future: rabbitmq.py, …)
    ├── object_store/     minio.py       (future: s3.py, …)
    └── observability/    langfuse.py    (future: langsmith.py, …)

Import the vendor module you need::

    from services.vector_store import qdrant
    from services.message_broker import kafka
    from services.object_store import minio
    from services.observability import langfuse

Rules (ai-agents/project_architecture.md §3): one folder per type, one module
per vendor, all modules in a folder share the same public function names; thin
and stateless; no imports from core/handler/router; configuration only via
``setting.settings``.
"""
