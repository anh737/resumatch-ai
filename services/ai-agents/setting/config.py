"""App configuration, read from environment variables / the service's .env file."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-agents/.env — anchored to the service folder so it is found no matter
# which directory the process is started from. Real env vars still win.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "ai-agents"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False

    # LLM (OpenAI)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4.1"
    OPENAI_TOOL_MODEL: str = ""            # model for tool selection; empty -> OPENAI_MODEL
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Agent
    AGENT_MAX_TOOL_ROUNDS: int = 3         # max tool-decide iterations per request
    AGENT_RETRIEVAL_TOP_K: int = 5         # default hits returned by the retrieval tools
    AGENT_SUGGESTIONS: int = 3             # follow-up prompts offered after each answer (0 disables)

    # Storage stack (xem storage/README.md)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_TIMEOUT: int = 10
    # Collections built by pre-processing/ (see services/vector_store/qdrant.py)
    QDRANT_CV_COLLECTION: str = "cv_information_technology"
    QDRANT_JD_COLLECTION: str = "jd_jobs"
    REDIS_URL: str = "redis://:changeme@localhost:6379/0"
    POSTGRES_URL: str = "postgresql://admin:changeme@localhost:5432/postgres"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:29092"
    KAFKA_CLIENT_ID: str = "ai-agents"
    KAFKA_CONSUMER_GROUP: str = "ai-agents"
    KAFKA_TOPIC_PREFIX: str = "resume-scan"
    KAFKA_TIMEOUT_MS: int = 10000
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "admin"
    MINIO_SECRET_KEY: str = "changeme"

    # Observability (Langfuse)
    # LANGFUSE_BASE_URL (the SDK's native variable name) is accepted as an alias.
    LANGFUSE_HOST: str = Field(
        "http://localhost:3031",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_ENABLED: bool = True          # master switch; also off when keys are empty
    LANGFUSE_ENVIRONMENT: str = "local"    # shown as "environment" on every trace
    LANGFUSE_RELEASE: str = ""             # e.g. git sha; optional
    LANGFUSE_SAMPLE_RATE: float = 1.0      # 0..1 fraction of traces to keep


settings = Settings()
