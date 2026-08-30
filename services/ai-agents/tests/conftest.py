"""Test defaults for the local storage stack (storage/README.md). Real env vars win."""

import os

os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("QDRANT_API_KEY", "changeme")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
os.environ.setdefault("KAFKA_TOPIC_PREFIX", "test")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")
