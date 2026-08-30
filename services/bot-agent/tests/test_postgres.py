"""services.database.postgres — live round trip against the local storage stack."""

import socket
import uuid
from urllib.parse import urlparse

import pytest

from services.database import postgres
from setting import settings


def _reachable() -> bool:
    parsed = urlparse(settings.POSTGRES_URL)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="PostgreSQL not reachable")


@pytest.fixture
async def conversation():
    await postgres.init_schema()
    cid = f"test-{uuid.uuid4().hex[:12]}"
    yield cid
    await postgres.delete_conversation(cid)
    await postgres.close_pool()


async def test_history_round_trip(conversation):
    cid = conversation
    assert await postgres.ping() is True

    await postgres.ensure_conversation(cid, title="first message")
    await postgres.ensure_conversation(cid, title="ignored — title is set once")

    for i in range(3):
        await postgres.insert_user_message(f"{cid}-u{i}", cid, f"question {i}")
        assert await postgres.insert_assistant_message(
            f"{cid}-u{i}:reply", cid, f"answer {i}",
            tool_calls=[{"tool": "retrieval_cv", "arguments": {"query": f"q{i}"}}],
            suggestions=[f"follow-up {i}"],
        )
    # duplicate delivery is a no-op
    assert await postgres.insert_assistant_message(f"{cid}-u0:reply", cid, "other") is False
    # failed turn: empty content must not appear in the forwarded history
    await postgres.insert_assistant_message(f"{cid}-fail:reply", cid, "", error="boom")

    turns = await postgres.recent_turns(cid, 2)  # 2 pairs -> last 4 non-empty messages
    assert [t["content"] for t in turns] == ["question 1", "answer 1", "question 2", "answer 2"]

    messages = await postgres.list_messages(cid)
    assert [m.id for m in messages][:2] == [f"{cid}-u0", f"{cid}-u0:reply"]
    assert messages[1].tool_calls == [{"tool": "retrieval_cv", "arguments": {"query": "q0"}}]
    assert messages[1].suggestions == ["follow-up 0"]
    assert messages[0].suggestions is None
    assert messages[-1].error == "boom"

    stored = {c.id: c for c in await postgres.list_conversations()}
    assert stored[cid].title == "first message"

    assert await postgres.delete_conversation(cid) is True
    assert await postgres.list_messages(cid) == []
