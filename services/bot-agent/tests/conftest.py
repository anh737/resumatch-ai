"""Test defaults for the local storage stack (storage/README.md). Real env vars win."""

import os

os.environ.setdefault("POSTGRES_URL", "postgresql://admin:changeme@localhost:5432/postgres")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
os.environ.setdefault("KAFKA_TOPIC_PREFIX", "test")
