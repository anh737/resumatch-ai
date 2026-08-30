"""core.agent — tool loop + summary against a fake OpenAI client (no network)."""

from types import SimpleNamespace

import pytest

import json

import core.agent as agent_module
from core.agent import FinalEvent, SuggestionsEvent, TokenEvent, ToolCallEvent, run_chat_agent
from schemas.chat import ChatTurn


# ---------------------------------------------------------------------------
# Fakes for the OpenAI SDK surface the agent touches
# ---------------------------------------------------------------------------
def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def _decide(*calls: SimpleNamespace) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    message = SimpleNamespace(tool_calls=list(calls), content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _stream(*tokens: str) -> list[SimpleNamespace]:
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))], usage=None)
        for t in tokens
    ]
    chunks.append(
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8, total_tokens=28))
    )
    return chunks


def _suggest(*suggestions: str) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=30, completion_tokens=6, total_tokens=36)
    message = SimpleNamespace(content=json.dumps({"suggestions": list(suggestions)}), tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeOpenAI:
    """Returns queued responses; a queued list is served as a stream."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if kwargs.get("stream"):
            async def gen():
                for chunk in item:
                    yield chunk
            return gen()
        return item


@pytest.fixture
def fake_openai(monkeypatch):
    def install(responses: list) -> FakeOpenAI:
        client = FakeOpenAI(responses)
        monkeypatch.setattr(agent_module, "get_async_llm_client", lambda: client)
        return client
    return install


@pytest.fixture
def fake_tools(monkeypatch):
    executed: list[tuple[str, dict]] = []

    async def execute(name, arguments):
        executed.append((name, arguments))
        return {"query": arguments.get("query", ""), "results": [{"resume_id": 7, "score": 0.9}]}

    monkeypatch.setattr(agent_module, "execute_tool", execute)
    return executed


async def _run(message="find me a python dev", history=()):
    return [event async for event in run_chat_agent(message, history)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_tool_then_finish_then_summary(fake_openai, fake_tools):
    client = fake_openai([
        _decide(_tool_call("c1", "retrieval_cv", '{"query": "python developer"}')),
        _decide(_tool_call("c2", "finish_tool_calls", "{}")),
        _stream("Hello", " world"),
        _suggest("Compare resume #7 with a job", "  Show resume #7 in detail  ", ""),
    ])

    events = await _run(history=[ChatTurn(role="user", content="hi"), ChatTurn(role="assistant", content="hello")])

    tool_events = [e for e in events if isinstance(e, ToolCallEvent)]
    tokens = [e.text for e in events if isinstance(e, TokenEvent)]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert tokens == ["Hello", " world"] and final.answer == "Hello world"
    assert [(e.tool, e.arguments) for e in tool_events] == [("retrieval_cv", {"query": "python developer"})]
    assert tool_events[0].result == {"result_count": 1, "ids": [7]}
    assert final.tool_calls[0].tool == "retrieval_cv" and final.tool_calls[0].error is None
    # suggestions are trimmed, blanks dropped, and precede the final event
    expected_suggestions = ["Compare resume #7 with a job", "Show resume #7 in detail"]
    assert isinstance(events[-2], SuggestionsEvent) and events[-2].suggestions == expected_suggestions
    assert final.suggestions == expected_suggestions
    # two decide rounds + summary + suggestions; usage aggregated across all four
    assert final.usage == {"input": 70, "output": 24, "total": 94}
    assert fake_tools == [("retrieval_cv", {"query": "python developer"})]

    decide_1, decide_2, summary, suggest = client.calls
    assert decide_1["tool_choice"] == "required" and len(decide_1["tools"]) == 3
    assert decide_1["messages"][-1] == {"role": "user", "content": "find me a python dev"}
    # the second round sees the tool transcript
    assert decide_2["messages"][-1]["role"] == "tool"
    assert summary["stream"] is True and "tools" not in summary
    assert summary["messages"][0]["role"] == "system"
    # the suggestion call sees the answer, uses structured output, no tools
    assert suggest["response_format"]["type"] == "json_schema" and "tools" not in suggest
    assert suggest["messages"][-1] == {"role": "assistant", "content": "Hello world"}


async def test_identical_repeat_breaks_the_loop(fake_openai, fake_tools):
    same = lambda cid: _tool_call(cid, "retrieval_jd", '{"query": "data engineer"}')  # noqa: E731
    client = fake_openai([
        _decide(same("c1")),
        _decide(same("c2")),   # identical repeat -> loop must stop (max rounds is 3)
        _stream("ok"),
        _suggest(),
    ])

    monkey_events = await _run()

    tool_events = [e for e in monkey_events if isinstance(e, ToolCallEvent)]
    assert len(tool_events) == 2
    assert tool_events[1].result == {"repeat_of_previous_call": True}
    assert len(fake_tools) == 1                      # executed once, replayed once
    assert len(client.calls) == 4                    # 2 decide rounds + summary + suggestions
    # empty suggestion list -> no SuggestionsEvent
    assert not [e for e in monkey_events if isinstance(e, SuggestionsEvent)]
    assert monkey_events[-1].suggestions == []


async def test_finish_first_round_skips_tools(fake_openai, fake_tools):
    fake_openai([
        _decide(_tool_call("c1", "finish_tool_calls", "{}")),
        _stream("Just chatting."),
        _suggest(),
    ])

    events = await _run(message="hello!")

    assert not [e for e in events if isinstance(e, ToolCallEvent)]
    assert events[-1].answer == "Just chatting."
    assert fake_tools == []


async def test_suggestion_failure_does_not_fail_turn(fake_openai, fake_tools):
    # No suggestion response queued: the third call raises inside the fake,
    # which the suggestion stage must swallow.
    fake_openai([
        _decide(_tool_call("c1", "finish_tool_calls", "{}")),
        _stream("Answer."),
    ])

    events = await _run()

    final = events[-1]
    assert isinstance(final, FinalEvent) and final.answer == "Answer."
    assert final.suggestions == []
    assert not [e for e in events if isinstance(e, SuggestionsEvent)]


async def test_suggestions_disabled(fake_openai, fake_tools, monkeypatch):
    monkeypatch.setattr(agent_module.settings, "AGENT_SUGGESTIONS", 0)
    client = fake_openai([
        _decide(_tool_call("c1", "finish_tool_calls", "{}")),
        _stream("Answer."),
    ])

    events = await _run()

    assert len(client.calls) == 2                    # decide + summary, no suggestion call
    assert events[-1].suggestions == []


async def test_tool_failure_is_reported_and_loop_stops(fake_openai, monkeypatch):
    async def boom(name, arguments):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(agent_module, "execute_tool", boom)
    client = fake_openai([
        _decide(_tool_call("c1", "retrieval_cv", '{"query": "x"}')),
        _stream("Sorry."),
        _suggest(),
    ])

    events = await _run()

    tool_event = next(e for e in events if isinstance(e, ToolCallEvent))
    assert tool_event.error == "RuntimeError: qdrant down"
    assert events[-1].tool_calls[0].error == "RuntimeError: qdrant down"
    # failed round makes no progress -> straight to summary, and the model saw
    # the friendly error string as the tool result
    assert len(client.calls) == 3
    assert client.calls[1]["messages"][-1]["content"].startswith("Sorry, this tool isn't accessible")
