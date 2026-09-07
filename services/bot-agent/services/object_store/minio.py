"""MinIO adapter — read/write the raw CV/JD files that bot-agent stores on upload.

Buckets hold the original uploaded files; the Kafka event carries the
``{bucket, key}`` pair this module resolves back to bytes.

Typical use::

    data = await minio.get_object("resumes", "1725270000123/cv.pdf")
    await minio.put_object("jobs", "upload_1/jd.pdf", data, content_type="application/pdf")

The underlying ``minio`` SDK is synchronous; every public coroutine pushes the
blocking call to a worker thread so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import io
from functools import lru_cache

from minio import Minio

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def get_minio() -> Minio:
    """Process-wide client (lazy)."""
    log.debug("connecting to MinIO at %s", settings.MINIO_ENDPOINT)
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def close_minio() -> None:
    """Drop the cached client (the SDK holds no persistent connection)."""
    get_minio.cache_clear()


async def ensure_bucket(bucket: str) -> None:
    """Create ``bucket`` if it does not exist (idempotent)."""

    def _ensure() -> None:
        client = get_minio()
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            log.info("created bucket %s", bucket)

    await asyncio.to_thread(_ensure)


async def put_object(bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
    """Store ``data`` under ``bucket/key`` (overwrites silently)."""

    def _put() -> None:
        get_minio().put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)

    await asyncio.to_thread(_put)
    log.debug("put %s/%s (%d bytes)", bucket, key, len(data))


async def get_object(bucket: str, key: str) -> bytes:
    """Read the full object at ``bucket/key``."""

    def _get() -> bytes:
        response = get_minio().get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    data = await asyncio.to_thread(_get)
    log.debug("got %s/%s (%d bytes)", bucket, key, len(data))
    return data


async def remove_object(bucket: str, key: str) -> None:
    """Delete ``bucket/key`` (no error if it is already gone)."""
    await asyncio.to_thread(get_minio().remove_object, bucket, key)


async def ping() -> bool:
    """True if MinIO answers (used by /health)."""
    try:
        await asyncio.to_thread(get_minio().list_buckets)
        return True
    except Exception as exc:  # noqa: BLE001 — health probes must never raise
        log.warning("MinIO ping failed: %s", exc)
        return False
