"""App configuration, read from environment variables / the service's .env file."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-embeddings/.env — anchored to the service folder so it is found no matter
# which directory the process is started from. Real env vars still win.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "ai-embeddings"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8002
    DEBUG: bool = False

    # LLM (OpenAI)
    OPENAI_API_KEY: str = ""
    OPENAI_STRUCTURE_MODEL: str = "gpt-4.1"                 # structuring model, = notebook GPT_MODEL
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"  # must match the offline pipeline
    EMBED_DIMS: int = 1536

    # Ingestion (values mirror the pre-processing notebooks)
    MAX_EMBED_TOKENS: int = 8000       # truncate once; headroom under the 8192 input cap
    EMBED_BATCH_SIZE: int = 64         # texts per embeddings request
    EMBED_BATCH_TOKENS: int = 250_000  # token budget per request (API cap 300k)
    CHUNK_PERCENTILE: float = 95.0     # SemanticChunker default breakpoint percentile
    CHUNK_BUFFER_SIZE: int = 1         # neighbor sentences combined per side before embedding
    CV_DEFAULT_CATEGORY: str = "INFORMATION-TECHNOLOGY"

    # Storage stack
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_TIMEOUT: float = 30.0
    QDRANT_CV_COLLECTION: str = "cv_information_technology"
    QDRANT_JD_COLLECTION: str = "jd_jobs"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:29092"
    KAFKA_CLIENT_ID: str = "ai-embeddings"
    KAFKA_CONSUMER_GROUP: str = "ai-embeddings"
    KAFKA_TOPIC_PREFIX: str = "resume-scan"
    KAFKA_TIMEOUT_MS: int = 10000
    MINIO_ENDPOINT: str = "localhost:9010"  # host port; compose overrides with minio:9000
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_SECURE: bool = False

    # Observability (Langfuse)
    LANGFUSE_HOST: str = Field(
        "http://localhost:3031",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_ENVIRONMENT: str = ""
    LANGFUSE_RELEASE: str = ""
    LANGFUSE_SAMPLE_RATE: float = 1.0


settings = Settings()
