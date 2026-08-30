"""App configuration, read from environment variables / the service's .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# bot-agent/.env — anchored to the service folder so it is found no matter
# which directory the process is started from. Real env vars still win.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "bot-agent"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8001
    DEBUG: bool = False

    # Chat history
    CHAT_HISTORY_TURNS: int = 10           # user+assistant pairs forwarded to ai-agents

    # Storage stack (see storage/README.md)
    POSTGRES_URL: str = "postgresql://admin:changeme@localhost:5432/postgres"
    POSTGRES_POOL_MIN: int = 1
    POSTGRES_POOL_MAX: int = 5
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:29092"
    KAFKA_CLIENT_ID: str = "bot-agent"
    KAFKA_CONSUMER_GROUP: str = "bot-agent"
    KAFKA_TOPIC_PREFIX: str = "resume-scan"
    KAFKA_TIMEOUT_MS: int = 10000


settings = Settings()
