"""App configuration, read from environment variables / the service's .env file."""

from pathlib import Path

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


settings = Settings()
