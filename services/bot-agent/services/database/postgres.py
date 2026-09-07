"""PostgreSQL adapter — chat-history + ingestion persistence on top of ``asyncpg``.

Three tables, created on startup by :func:`init_schema`:

* ``conversations`` — one row per conversation (id, title, timestamps). Ids are
  TEXT because the front-end generates them client-side.
* ``chat_messages`` — one row per turn. ``seq`` (identity column) gives a
  total order that is stable even when rows share a timestamp. Assistant rows
  carry the executed ``tool_calls`` and the follow-up ``suggestions`` (both
  JSONB) plus an ``error`` when the agent failed; their id is derived from the
  user message id (``<id>:reply``) so the at-least-once Kafka consumer can
  insert idempotently.

* ``ingestion_uploads`` — one row per CV/JD file an admin uploaded. bot-agent
  inserts it as ``queued`` when the file lands in MinIO and the Kafka upload
  event is produced; the ``resume.processed`` / ``job.processed`` consumer
  moves it through ``processing`` to ``done``/``failed`` (terminal states never
  regress on redelivered events).

:func:`recent_turns` returns the last ``turns`` user+assistant pairs (i.e. up
to ``2 * turns`` non-empty messages), oldest first — the history bot-agent
forwards to ai-agents with every request.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq              BIGINT GENERATED ALWAYS AS IDENTITY,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL DEFAULT '',
    tool_calls       JSONB,
    suggestions      JSONB,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Upgrade path for databases created before the column existed.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS suggestions JSONB;

CREATE INDEX IF NOT EXISTS chat_messages_conversation_seq
    ON chat_messages (conversation_id, seq);

CREATE TABLE IF NOT EXISTS ingestion_uploads (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('cv', 'jd')),
    document_id  TEXT NOT NULL,
    filename     TEXT NOT NULL,
    bucket       TEXT NOT NULL,
    object_key   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    points       INTEGER,
    error        TEXT,
    trace_id     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestion_uploads_created
    ON ingestion_uploads (created_at DESC);

-- Upgrade path: CV category requested at upload time (kept so a re-process reuses it).
ALTER TABLE ingestion_uploads ADD COLUMN IF NOT EXISTS category TEXT;
"""


@dataclass(slots=True)
class MessageRecord:
    """One stored chat message."""

    id: str
    conversation_id: str
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None
    suggestions: list[str] | None
    error: str | None
    created_at: datetime


@dataclass(slots=True)
class ConversationRecord:
    """One stored conversation."""

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class UploadRecord:
    """One admin-uploaded CV/JD file and its ingestion status."""

    id: str
    kind: str
    document_id: str
    filename: str
    bucket: str
    object_key: str
    status: str
    points: int | None
    error: str | None
    trace_id: str | None
    created_at: datetime
    updated_at: datetime
    category: str | None = None


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Process-wide connection pool, created on first use."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=settings.POSTGRES_URL,
                min_size=settings.POSTGRES_POOL_MIN,
                max_size=settings.POSTGRES_POOL_MAX,
            )
            log.info("PostgreSQL pool connected (%s)", settings.POSTGRES_URL.rsplit("@", 1)[-1])
    return _pool


async def close_pool() -> None:
    """Close the shared pool (FastAPI shutdown hook)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL pool closed")


async def ping() -> bool:
    """True if the database answers (used by /health)."""
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        return True
    except Exception as exc:  # noqa: BLE001 — health probes must never raise
        log.warning("PostgreSQL ping failed: %s", exc)
        return False


async def init_schema() -> None:
    """Create the chat-history tables if they do not exist (startup hook)."""
    pool = await get_pool()
    await pool.execute(_SCHEMA)
    log.info("chat-history schema ensured")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
async def ensure_conversation(conversation_id: str, title: str | None = None) -> None:
    """Create the conversation if new; fill in the title once, bump updated_at."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO conversations (id, title) VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE
            SET title = COALESCE(conversations.title, EXCLUDED.title),
                updated_at = now()
        """,
        conversation_id,
        title,
    )


async def insert_user_message(message_id: str, conversation_id: str, content: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO chat_messages (id, conversation_id, role, content) VALUES ($1, $2, 'user', $3)",
        message_id,
        conversation_id,
        content,
    )


async def insert_assistant_message(
    message_id: str,
    conversation_id: str,
    content: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    suggestions: list[str] | None = None,
    error: str | None = None,
) -> bool:
    """Store one assistant turn; returns False when ``message_id`` already exists
    (redelivered Kafka message) so callers can log the duplicate."""
    pool = await get_pool()
    result = await pool.execute(
        """
        INSERT INTO chat_messages (id, conversation_id, role, content, tool_calls, suggestions, error)
        VALUES ($1, $2, 'assistant', $3, $4::jsonb, $5::jsonb, $6)
        ON CONFLICT (id) DO NOTHING
        """,
        message_id,
        conversation_id,
        content,
        json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else None,
        json.dumps(suggestions, ensure_ascii=False) if suggestions else None,
        error,
    )
    inserted = result.endswith("1")
    if inserted:
        await pool.execute("UPDATE conversations SET updated_at = now() WHERE id = $1", conversation_id)
    return inserted


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _message(row: asyncpg.Record) -> MessageRecord:
    raw_tools, raw_suggestions = row["tool_calls"], row["suggestions"]
    return MessageRecord(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        tool_calls=json.loads(raw_tools) if raw_tools else None,
        suggestions=json.loads(raw_suggestions) if raw_suggestions else None,
        error=row["error"],
        created_at=row["created_at"],
    )


async def recent_turns(conversation_id: str, turns: int | None = None) -> list[dict[str, str]]:
    """Last ``turns`` user+assistant pairs as ``{"role", "content"}`` dicts, oldest first.

    Empty messages (failed assistant turns) are skipped so the agent never sees
    blank history entries.
    """
    limit = 2 * (turns if turns is not None else settings.CHAT_HISTORY_TURNS)
    if limit <= 0:
        return []
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT role, content FROM (
            SELECT role, content, seq FROM chat_messages
            WHERE conversation_id = $1 AND content <> ''
            ORDER BY seq DESC
            LIMIT $2
        ) latest ORDER BY seq
        """,
        conversation_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def list_messages(conversation_id: str) -> list[MessageRecord]:
    """Every stored message of one conversation, oldest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, conversation_id, role, content, tool_calls, suggestions, error, created_at
        FROM chat_messages WHERE conversation_id = $1 ORDER BY seq
        """,
        conversation_id,
    )
    return [_message(r) for r in rows]


async def list_conversations(limit: int = 100) -> list[ConversationRecord]:
    """Conversations, most recently active first."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT $1",
        limit,
    )
    return [ConversationRecord(id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows]


async def delete_conversation(conversation_id: str) -> bool:
    """Remove a conversation and (via cascade) its messages."""
    pool = await get_pool()
    result = await pool.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Ingestion uploads
# ---------------------------------------------------------------------------
def _upload(row: asyncpg.Record) -> UploadRecord:
    return UploadRecord(
        id=row["id"],
        kind=row["kind"],
        document_id=row["document_id"],
        filename=row["filename"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        status=row["status"],
        points=row["points"],
        error=row["error"],
        trace_id=row["trace_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        category=row["category"],
    )


async def insert_upload(
    upload_id: str,
    *,
    kind: str,
    document_id: str,
    filename: str,
    bucket: str,
    object_key: str,
    category: str | None = None,
) -> UploadRecord:
    """Record a fresh upload (status ``queued``) and return the stored row."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO ingestion_uploads (id, kind, document_id, filename, bucket, object_key, category)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        upload_id,
        kind,
        document_id,
        filename,
        bucket,
        object_key,
        category,
    )
    return _upload(row)


async def reset_upload(upload_id: str) -> UploadRecord | None:
    """Put a row back to ``queued`` before its upload event is re-produced (re-process).

    Clears the previous outcome (points, error, trace) so the admin table
    shows the new run; ``None`` when the id is unknown.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE ingestion_uploads
        SET status = 'queued', points = NULL, error = NULL, trace_id = NULL, updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        upload_id,
    )
    return _upload(row) if row else None


async def update_upload_status(
    upload_id: str,
    *,
    status: str,
    points: int | None = None,
    error: str | None = None,
    trace_id: str | None = None,
) -> bool:
    """Apply a ``*.processed`` event; terminal states never regress.

    A redelivered ``processing`` event after ``done``/``failed`` is a no-op, so
    the at-least-once Kafka consumer stays idempotent. Returns False when no
    row changed (unknown id or ignored regression).
    """
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE ingestion_uploads
        SET status = $2,
            points = COALESCE($3, points),
            error = $4,
            trace_id = COALESCE($5, trace_id),
            updated_at = now()
        WHERE id = $1
          AND NOT (status IN ('done', 'failed') AND $2 = 'processing')
        """,
        upload_id,
        status,
        points,
        error,
        trace_id,
    )
    return result.endswith("1")


async def list_uploads(limit: int = 50, *, kind: str | None = None, status: str | None = None) -> list[UploadRecord]:
    """Uploads, newest first (the admin portal's tables); optional ``kind`` / ``status`` filters."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM ingestion_uploads
        WHERE ($2::text IS NULL OR kind = $2)
          AND ($3::text IS NULL OR status = $3)
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
        kind,
        status,
    )
    return [_upload(r) for r in rows]


async def get_upload(upload_id: str) -> UploadRecord | None:
    """One upload row, or ``None`` when the id is unknown."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM ingestion_uploads WHERE id = $1", upload_id)
    return _upload(row) if row else None


@dataclass(slots=True)
class UploadStatsRow:
    """Aggregate of ``ingestion_uploads`` for one (kind, status) pair."""

    kind: str
    status: str
    count: int
    points: int


async def upload_stats() -> tuple[list[UploadStatsRow], datetime | None]:
    """Counts + upserted points per (kind, status), and the newest upload time."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT kind, status, COUNT(*)::int AS count, COALESCE(SUM(points), 0)::int AS points
        FROM ingestion_uploads
        GROUP BY kind, status
        """
    )
    last = await pool.fetchval("SELECT MAX(created_at) FROM ingestion_uploads")
    return [UploadStatsRow(kind=r["kind"], status=r["status"], count=r["count"], points=r["points"]) for r in rows], last
