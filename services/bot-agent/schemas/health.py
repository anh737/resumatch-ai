from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    postgres: bool | None = None
    kafka: bool | None = None
