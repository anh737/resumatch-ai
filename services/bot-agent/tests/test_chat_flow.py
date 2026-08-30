"""handler.chat — submit + response persistence with fake Postgres/Kafka."""

import pytest
from pydantic import ValidationError

import handler.chat as chat_handler
from schemas.chat import ChatRequest
from services.database import postgres
from services.message_broker import kafka


@pytest.fixture
def db(monkeypatch):
    """Record every postgres call in order; recent_turns returns canned history."""
    calls: list[tuple] = []

    async def ensure_conversation(cid, title=None):
        calls.append(("ensure", cid, title))

    async def recent_turns(cid, turns=None):
        calls.append(("history", cid, turns))
        return [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]

    async def insert_user_message(mid, cid, content):
        calls.append(("user", mid, cid, content))

    async def insert_assistant_message(mid, cid, content, *, tool_calls=None, suggestions=None, error=None):
        calls.append(("assistant", mid, cid, content, tool_calls, suggestions, error))
        return not any(c[0] == "assistant" and c[1] == mid for c in calls[:-1])  # duplicate -> False

    for name, fn in [("ensure_conversation", ensure_conversation), ("recent_turns", recent_turns),
                     ("insert_user_message", insert_user_message),
                     ("insert_assistant_message", insert_assistant_message)]:
        monkeypatch.setattr(postgres, name, fn)
    return calls


@pytest.fixture
def produced(monkeypatch):
    out: list[tuple[str, dict, str]] = []

    async def produce(topic, value, *, key=None, **_):
        out.append((topic, value, key))
        return kafka.Delivery(topic=topic, partition=0, offset=0, timestamp_ms=0)

    monkeypatch.setattr(kafka, "produce", produce)
    return out


async def test_submit_persists_then_queues_with_history(db, produced):
    accepted = await chat_handler.submit_chat(
        ChatRequest(conversation_id="conv-1", message="find a python dev")
    )

    assert accepted.conversation_id == "conv-1" and accepted.status == "queued"
    # history is read BEFORE the new user message is stored
    assert [c[0] for c in db] == ["ensure", "history", "user"]
    assert db[2][3] == "find a python dev"

    (topic, value, key), = produced
    assert topic == kafka.TOPIC_CHAT_REQUESTS and key == "conv-1"
    assert value == {
        "conversation_id": "conv-1",
        "message_id": accepted.message_id,
        "message": "find a python dev",
        "history": [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}],
    }


async def test_submit_generates_conversation_id_and_accepts_legacy_shape(db, produced):
    accepted = await chat_handler.submit_chat(
        ChatRequest(messages=[{"role": "assistant", "content": "old"}, {"role": "user", "content": "hello"}])
    )
    assert accepted.conversation_id  # generated
    assert produced[0][1]["message"] == "hello"


def test_request_without_user_message_is_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(conversation_id="c", messages=[{"role": "assistant", "content": "only me"}])


async def test_response_is_persisted_idempotently(db, produced):
    msg = kafka.Message(
        topic="resume-scan.chat.responses",
        key="conv-1",
        value={
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "answer": "Here you go",
            "tool_calls": [{"tool": "retrieval_cv", "arguments": {"query": "python"}}],
            "suggestions": ["Show resume #7 in detail"],
        },
    )
    await chat_handler.handle_chat_response(msg)
    await chat_handler.handle_chat_response(msg)  # redelivery must be a no-op

    inserts = [c for c in db if c[0] == "assistant"]
    assert len(inserts) == 2 and inserts[0][1] == "msg-1:reply"
    assert inserts[0][3] == "Here you go"
    assert inserts[0][4][0]["tool"] == "retrieval_cv"
    assert inserts[0][5] == ["Show resume #7 in detail"]


async def test_invalid_response_payload_raises_for_dead_letter(db):
    with pytest.raises(ValidationError):
        await chat_handler.handle_chat_response(
            kafka.Message(topic="t", key=None, value={"answer": "no ids"})
        )
    assert db == []
