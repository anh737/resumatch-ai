# ai-embeddings — online ingestion service (Kafka in, Qdrant out).
# Build from services/:  docker compose build ai-embeddings
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the cl100k_base BPE file into the image so truncation works offline.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY . .

RUN useradd --create-home appuser
USER appuser

EXPOSE 8002
# No --reload in the image; DEBUG only raises log verbosity here.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
