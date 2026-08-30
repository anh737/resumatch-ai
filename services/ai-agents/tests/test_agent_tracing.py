"""core.agent × Langfuse — the trace layout reaches the (mock) server.

Reuses the mock Langfuse server from ``test_langfuse`` and the fake OpenAI
client from ``test_agent``: a full agent run must export the agent-tool /
agent-summary observations with their status fields, the per-round
``tool_decide`` generations, the per-call ``tool:*`` spans and the
``suggestions`` generation.
"""

from services.observability import langfuse

# Fixtures resolved by name: mock_server/configured (Langfuse), fake_openai/fake_tools (agent).
from test_agent import _decide, _stream, _suggest, _tool_call, fake_openai, fake_tools  # noqa: F401
from test_langfuse import _bodies, configured, mock_server  # noqa: F401

from core.agent import run_chat_agent


async def _drain(agent):
    return [event async for event in agent]


async def test_full_run_exports_agent_status(configured, fake_openai, fake_tools):
    fake_openai([
        _decide(_tool_call("c1", "retrieval_cv", '{"query": "python developer"}')),
        _decide(_tool_call("c2", "finish_tool_calls", "{}")),
        _stream("Hello", " world"),
        _suggest("Show resume #7 in detail"),
    ])

    await _drain(run_chat_agent("find me a python dev"))
    langfuse.flush()

    blob = _bodies()
    for needle in [
        b"agent-tool", b"agent-summary",            # the two agent observations
        b"tool_decide:0", b"tool_decide:1",         # reasoning generation per round
        b"tool:retrieval_cv",                       # executed tool span
        b"summary", b"suggestions",                 # answer + follow-up generations
        b"stop_reason", b"finish_tool_calls",       # loop status on agent-tool
        b"Hello world",                             # answer as agent-summary output
    ]:
        assert needle in blob, f"{needle!r} not exported"


async def test_tool_failure_exports_error_status(configured, fake_openai, monkeypatch):
    import core.agent as agent_module

    async def boom(name, arguments):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(agent_module, "execute_tool", boom)
    fake_openai([
        _decide(_tool_call("c1", "retrieval_cv", '{"query": "x"}')),
        _stream("Sorry."),
        _suggest(),
    ])

    await _drain(run_chat_agent("hi"))
    langfuse.flush()

    blob = _bodies()
    assert b"RuntimeError: qdrant down" in blob     # ERROR status on the tool span
    assert b"no_progress" in blob                   # loop stop reason
