from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    # Filled only by /health?probe=true
    qdrant: bool | None = None
    kafka: bool | None = None
    minio: bool | None = None
    langfuse: bool | None = None
