"""services — adapters for external systems, grouped by service *type*.

    services/
    ├── vector_store/     qdrant.py      (future: chromadb.py, …)
    ├── message_broker/   kafka.py       (future: rabbitmq.py, …)
    └── observability/    langfuse.py    (future: langsmith.py, …)
    # planned: cache/ (redis), object_store/ (minio), database/ (postgres)

Import the vendor module you need::

    from services.vector_store import qdrant
    from services.message_broker import kafka
    from services.observability import langfuse

Rules (project_architecture.md §3): one folder per type, one module per vendor,
all modules in a folder share the same public function names; thin and
stateless; no imports from core/handler/router; configuration only via
``setting.settings``.
"""
