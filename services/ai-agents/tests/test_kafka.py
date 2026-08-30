"""services.message_broker.kafka — live produce / consume / dead-letter against the local broker."""

import asyncio
import socket
import uuid

import pytest
from aiokafka.admin import AIOKafkaAdminClient

from services.message_broker import kafka
from setting import settings


def _reachable() -> bool:
    host, _, port = settings.KAFKA_BOOTSTRAP_SERVERS.partition(":")
    try:
        with socket.create_connection((host, int(port or 9092)), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="Kafka not reachable")


def test_topic_name_prefix_once():
    assert kafka.topic_name("chat.events") == f"{settings.KAFKA_TOPIC_PREFIX}.chat.events"
    assert kafka.topic_name(kafka.topic_name("chat.events")) == kafka.topic_name("chat.events")


async def _delete(*names: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    await admin.start()
    try:
        await admin.delete_topics([kafka.topic_name(n) for n in names])
    finally:
        await admin.close()


async def test_roundtrip_and_dead_letter():
    run = uuid.uuid4().hex[:8]
    topic = f"smoke.{run}"
    dead = kafka.TOPIC_DEAD_LETTER
    try:
        assert await kafka.ping() is True
        created = await kafka.ensure_topics([topic, dead])
        assert kafka.topic_name(topic) in created
        assert await kafka.ensure_topics([topic]) == []  # idempotent
        assert kafka.topic_name(topic) in await kafka.list_topics()

        d = await kafka.produce(topic, {"n": 1, "text": "xin chào"}, key="a", headers={"source": "test"})
        assert d.topic == kafka.topic_name(topic) and d.offset >= 0
        await kafka.produce(topic, {"n": 2, "boom": True}, key="bad")
        await kafka.produce_many(topic, [("c", {"n": 3}), ("d", {"n": 4})])

        seen: list[kafka.Message] = []
        stop = asyncio.Event()

        async def handler(msg: kafka.Message) -> None:
            seen.append(msg)
            if msg.key == "bad":
                raise RuntimeError("handler exploded")
            if len(seen) == 4:
                stop.set()

        handled = await asyncio.wait_for(
            kafka.consume([topic], handler, group_id=f"g-{run}", stop=stop, poll_timeout_ms=300), timeout=30
        )
        assert handled == 4
        assert [m.value["n"] for m in seen] == [1, 2, 3, 4]
        assert seen[0].key == "a" and seen[0].headers == {"source": "test"} and seen[0].value["text"] == "xin chào"

        # the failing record must have been forwarded to the dead-letter topic
        dl: list[kafka.Message] = []
        stop_dl = asyncio.Event()

        async def dl_handler(msg: kafka.Message) -> None:
            if msg.value.get("topic") == kafka.topic_name(topic):
                dl.append(msg)
                stop_dl.set()

        await asyncio.wait_for(
            kafka.consume([dead], dl_handler, group_id=f"dl-{run}", stop=stop_dl, poll_timeout_ms=300), timeout=30
        )
        assert dl and dl[0].value["key"] == "bad" and "handler exploded" in dl[0].value["error"]
        assert dl[0].headers["source_topic"] == kafka.topic_name(topic)
    finally:
        await kafka.stop_producer()
        await _delete(topic, dead)


async def test_start_and_stop_consumer_task():
    run = uuid.uuid4().hex[:8]
    topic = f"smoke.{run}"
    try:
        await kafka.ensure_topics([topic])
        task = kafka.start_consumer([topic], lambda m: asyncio.sleep(0), group_id=f"g-{run}", poll_timeout_ms=200)
        await asyncio.sleep(1.5)
        assert not task.done()
        await kafka.stop_consumer(task)
        assert task.done()
    finally:
        await _delete(topic)
