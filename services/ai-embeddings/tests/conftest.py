"""Shared test setup — neutralize env so tests never talk to real backends."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")
