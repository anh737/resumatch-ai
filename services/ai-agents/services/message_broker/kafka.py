"""Kafka adapter — JSON producer and consumer on top of ``aiokafka``.

Topics are namespaced with ``settings.KAFKA_TOPIC_PREFIX`` (``resume-scan`` by
default), e.g. ``resume-scan.resume.uploaded``. Messages are JSON objects; the
optional key is a string (use the entity id so all events of one resume/job
land on the same partition and stay ordered).

Typical use::

    # producer (anywhere in a request handler)
    await kafka.produce(kafka.TOPIC_RESUME_UPLOADED, {"resume_id": 42, "bucket": "resumes", "key": "42.pdf"}, key="42")

    # consumer (FastAPI lifespan)
    async def handle(msg: kafka.Message) -> None: ...
    task = kafka.start_consumer([kafka.TOPIC_RESUME_UPLOADED], handle)
    ...
    await kafka.stop_consumer(task); await kafka.stop_producer()

Delivery semantics: producer is idempotent with ``acks=all``; the consumer
commits each offset only after the handler returned, i.e. *at-least-once* — make
handlers idempotent. A failing handler sends the message to the dead-letter
topic by default (``on_error="dead-letter"``) and moves on.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Sequence

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError
from aiokafka.structs import ConsumerRecord

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------
TOPIC_RESUME_UPLOADED = "resume.uploaded"      # a new resume file landed in MinIO
TOPIC_RESUME_PROCESSED = "resume.processed"    # extraction + embedding finished
TOPIC_JOB_UPSERTED = "job.upserted"            # a job description was (re)indexed
TOPIC_CHAT_REQUESTS = "chat.requests"          # bot-agent -> ai-agents: user turn + history
TOPIC_CHAT_RESPONSES = "chat.responses"        # ai-agents -> bot-agent: answer + tool calls
TOPIC_CHAT_EVENTS = "chat.events"              # analytics: chat turns, feedback
TOPIC_DEAD_LETTER = "dead-letter"              # messages whose handler failed


def topic_name(name: str) -> str:
    """Fully qualified topic name (prefix applied once)."""
    prefix = settings.KAFKA_TOPIC_PREFIX.strip(".")
    if not prefix or name.startswith(prefix + "."):
        return name
    return f"{prefix}.{name}"


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Message:
    """A consumed record with the JSON value already decoded."""

    topic: str
    key: str | None
    value: Any
    headers: dict[str, str] = field(default_factory=dict)
    partition: int = 0
    offset: int = 0
    timestamp_ms: int = 0


@dataclass(slots=True)
class Delivery:
    """Broker acknowledgement for a produced record."""

    topic: str
    partition: int
    offset: int
    timestamp_ms: int


Handler = Callable[[Message], Awaitable[None]]
ErrorPolicy = Literal["dead-letter", "skip", "raise"]


def _encode_value(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _decode_value(raw: bytes | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


def _encode_headers(headers: Mapping[str, str] | None) -> list[tuple[str, bytes]]:
    return [(k, str(v).encode("utf-8")) for k, v in (headers or {}).items()]


def _to_message(rec: ConsumerRecord) -> Message:
    return Message(
        topic=rec.topic,
        key=rec.key.decode("utf-8", errors="replace") if rec.key else None,
        value=_decode_value(rec.value),
        headers={k: v.decode("utf-8", errors="replace") for k, v in (rec.headers or [])},
        partition=rec.partition,
        offset=rec.offset,
        timestamp_ms=rec.timestamp,
    )


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------
_producer: AIOKafkaProducer | None = None
_producer_lock = asyncio.Lock()


async def get_producer() -> AIOKafkaProducer:
    """Process-wide producer, started on first use."""
    global _producer
    if _producer is not None:
        return _producer
    async with _producer_lock:
        if _producer is None:
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                client_id=settings.KAFKA_CLIENT_ID,
                acks="all",
                enable_idempotence=True,
                compression_type="gzip",
                request_timeout_ms=settings.KAFKA_TIMEOUT_MS,
            )
            await producer.start()
            _producer = producer
            log.info("Kafka producer connected to %s", settings.KAFKA_BOOTSTRAP_SERVERS)
    return _producer


async def produce(
    topic: str,
    value: Any,
    *,
    key: str | None = None,
    headers: Mapping[str, str] | None = None,
    partition: int | None = None,
) -> Delivery:
    """Publish one JSON message and wait for the broker acknowledgement."""
    producer = await get_producer()
    name = topic_name(topic)
    meta = await producer.send_and_wait(
        name,
        value=_encode_value(value),
        key=key.encode("utf-8") if key is not None else None,
        headers=_encode_headers(headers),
        partition=partition,
    )
    log.debug("produced %s key=%s -> partition %s offset %s", name, key, meta.partition, meta.offset)
    return Delivery(topic=meta.topic, partition=meta.partition, offset=meta.offset, timestamp_ms=meta.timestamp)


async def produce_many(topic: str, items: Iterable[tuple[str | None, Any]]) -> list[Delivery]:
    """Publish ``(key, value)`` pairs, batching them into as few requests as possible."""
    producer = await get_producer()
    name = topic_name(topic)
    futures = [
        await producer.send(name, value=_encode_value(v), key=k.encode("utf-8") if k is not None else None)
        for k, v in items
    ]
    metas = await asyncio.gather(*futures)
    return [Delivery(topic=m.topic, partition=m.partition, offset=m.offset, timestamp_ms=m.timestamp) for m in metas]


async def stop_producer() -> None:
    """Flush and close the shared producer (FastAPI shutdown hook)."""
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None
        log.info("Kafka producer stopped")


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------
def create_consumer(
    topics: Sequence[str],
    *,
    group_id: str | None = None,
    auto_offset_reset: Literal["earliest", "latest"] = "earliest",
) -> AIOKafkaConsumer:
    """Un-started consumer with manual commits; use :func:`consume` unless you need raw access."""
    return AIOKafkaConsumer(
        *[topic_name(t) for t in topics],
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        client_id=settings.KAFKA_CLIENT_ID,
        group_id=group_id or settings.KAFKA_CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
        request_timeout_ms=settings.KAFKA_TIMEOUT_MS,
    )


async def consume(
    topics: Sequence[str],
    handler: Handler,
    *,
    group_id: str | None = None,
    auto_offset_reset: Literal["earliest", "latest"] = "earliest",
    on_error: ErrorPolicy = "dead-letter",
    stop: asyncio.Event | None = None,
    poll_timeout_ms: int = 1000,
    max_records: int = 100,
) -> int:
    """Run ``handler`` for every message on ``topics`` until ``stop`` is set or the task is cancelled.

    Offsets are committed per record *after* the handler succeeds. On handler
    failure: ``"dead-letter"`` forwards the record (with the error) to
    :data:`TOPIC_DEAD_LETTER` and continues, ``"skip"`` just logs and continues,
    ``"raise"`` stops the loop and re-raises. Returns the number of handled records.
    """
    consumer = create_consumer(topics, group_id=group_id, auto_offset_reset=auto_offset_reset)
    await consumer.start()
    log.info("Kafka consumer %s subscribed to %s", consumer._group_id, [topic_name(t) for t in topics])
    handled = 0
    try:
        while stop is None or not stop.is_set():
            batches = await consumer.getmany(timeout_ms=poll_timeout_ms, max_records=max_records)
            for tp, records in batches.items():
                for rec in records:
                    msg = _to_message(rec)
                    try:
                        await handler(msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — policy decides
                        log.exception("handler failed for %s[%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
                        if on_error == "raise":
                            raise
                        if on_error == "dead-letter":
                            await produce(
                                TOPIC_DEAD_LETTER,
                                {"topic": msg.topic, "key": msg.key, "value": msg.value, "error": repr(exc),
                                 "partition": msg.partition, "offset": msg.offset},
                                key=msg.key,
                                headers={"source_topic": msg.topic},
                            )
                    await consumer.commit({tp: rec.offset + 1})
                    handled += 1
    finally:
        await consumer.stop()
        log.info("Kafka consumer stopped after %d records", handled)
    return handled


def start_consumer(topics: Sequence[str], handler: Handler, **kwargs: Any) -> asyncio.Task[int]:
    """Run :func:`consume` as a background task (call from the FastAPI lifespan)."""
    return asyncio.create_task(consume(topics, handler, **kwargs), name=f"kafka-consumer:{','.join(topics)}")


async def stop_consumer(task: asyncio.Task[int], *, timeout: float = 10.0) -> None:
    """Cancel a background consumer and wait for it to close its connection."""
    if task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


# ---------------------------------------------------------------------------
# Admin / health
# ---------------------------------------------------------------------------
async def ensure_topics(names: Sequence[str], *, partitions: int = 1, replication_factor: int = 1) -> list[str]:
    """Create the topics that do not exist yet (single-broker defaults). Returns the created names."""
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, client_id=settings.KAFKA_CLIENT_ID)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        wanted = [topic_name(n) for n in names]
        missing = [n for n in wanted if n not in existing]
        if not missing:
            return []
        try:
            await admin.create_topics([NewTopic(n, num_partitions=partitions, replication_factor=replication_factor) for n in missing])
        except TopicAlreadyExistsError:
            pass
        log.info("created Kafka topics %s", missing)
        return missing
    finally:
        await admin.close()


async def list_topics() -> list[str]:
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, client_id=settings.KAFKA_CLIENT_ID)
    await admin.start()
    try:
        return sorted(await admin.list_topics())
    finally:
        await admin.close()


async def ping() -> bool:
    """True if a broker answers (used by /health)."""
    try:
        await list_topics()
        return True
    except (KafkaError, OSError, asyncio.TimeoutError) as exc:
        log.warning("Kafka ping failed: %s", exc)
        return False
