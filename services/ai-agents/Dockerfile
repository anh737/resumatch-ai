# ai-agents — FastAPI agent service (Kafka in, WebSocket out).
# Build from services/:  docker compose build ai-agents
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
# No --reload in the image; DEBUG only raises log verbosity here.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
