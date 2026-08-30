"""handler.chat — Kafka message in, WebSocket events + chat.responses out (all fakes)."""

import pytest
from pydantic import ValidationError

import handler.chat as chat_handler
from core.agent import FinalEvent, SuggestionsEvent, TokenEvent, ToolCallEvent
from schemas.chat import ToolCallRecord
from services.message_broker import kafka


class ManagerStub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, conversation_id, payload):
        self.sent.append((conversation_id, payload))
        return 1


def _request_msg(value) -> kafka.Message:
    return kafka.Message(topic="resume-scan.chat.requests", key="conv-1", value=value)


VALID = {
    "conversation_id": "conv-1",
    "message_id": "msg-1",
    "message": "find a python dev",
    "history": [{"role": "user", "content": "hi"}],
}


@pytest.fixture
def ws(monkeypatch):
    stub = ManagerStub()
    monkeypatch.setattr(chat_handler, "manager", stub)
    return stub


@pytest.fixture
def produced(monkeypatch):
    out: list[tuple[str, dict, str]] = []

    async def produce(topic, value, *, key=None, **_):
        out.append((topic, value, key))
        return kafka.Delivery(topic=topic, partition=0, offset=0, timestamp_ms=0)

    monkeypatch.setattr(kafka, "produce", produce)
    return out


async def test_happy_path_streams_and_persists(monkeypatch, ws, produced):
    async def fake_agent(message, history):
        assert message == "find a python dev" and len(history) == 1
        yield ToolCallEvent(tool="retrieval_cv", arguments={"query": "python"}, result={"result_count": 1})
        yield TokenEvent(text="Here ")
        yield TokenEvent(text="you go")
        yield SuggestionsEvent(suggestions=["Show resume #7 in detail"])
        yield FinalEvent(answer="Here you go",
                         tool_calls=[ToolCallRecord(tool="retrieval_cv", arguments={"query": "python"})],
                         suggestions=["Show resume #7 in detail"],
                         usage={"input": 1, "output": 2, "total": 3})

    monkeypatch.setattr(chat_handler, "run_chat_agent", fake_agent)

    await chat_handler.handle_chat_request(_request_msg(VALID))

    payloads = [p for _, p in ws.sent]
    assert payloads[0] == {"type": "tool_call", "tool": "retrieval_cv", "arguments": {"query": "python"}}
    assert [p["content"] for p in payloads[1:3]] == ["Here ", "you go"]
    assert payloads[-2] == {"type": "suggestions", "suggestions": ["Show resume #7 in detail"]}
    assert payloads[-1] == {"type": "done", "conversation_id": "conv-1"}

    (topic, value, key), = produced
    assert topic == kafka.TOPIC_CHAT_RESPONSES and key == "conv-1"
    assert value["answer"] == "Here you go" and value["error"] is None
    assert value["message_id"] == "msg-1"
    assert value["tool_calls"][0]["tool"] == "retrieval_cv"
    assert value["suggestions"] == ["Show resume #7 in detail"]
    assert value["usage"] == {"input": 1, "output": 2, "total": 3}


async def test_agent_failure_reaches_both_channels(monkeypatch, ws, produced):
    async def failing_agent(message, history):
        yield TokenEvent(text="partial")
        raise RuntimeError("llm exploded")

    monkeypatch.setattr(chat_handler, "run_chat_agent", failing_agent)

    await chat_handler.handle_chat_request(_request_msg(VALID))  # must not raise

    assert ws.sent[-1][1]["type"] == "error"
    (_, value, _), = produced
    assert value["error"] == "RuntimeError: llm exploded" and value["answer"] == ""


async def test_invalid_payload_goes_to_dead_letter(ws, produced):
    with pytest.raises(ValidationError):
        await chat_handler.handle_chat_request(_request_msg({"nope": True}))
    assert ws.sent == [] and produced == []
