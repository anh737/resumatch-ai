"""Agent runtime — tool-call loop (ai-agent) + answer stage (ai-agent-summary).

Two-stage design (project architecture §5), on the plain OpenAI SDK:

1. **Tool loop** — ``OPENAI_TOOL_MODEL`` is bound to the retrieval tools with
   ``tool_choice="required"`` and asked which to call next. It keeps calling
   ``retrieval_cv`` / ``retrieval_jd`` until it signals ``finish_tool_calls``,
   repeats an identical call (loop guard), or ``AGENT_MAX_TOOL_ROUNDS`` is hit.
2. **Summary** — ``OPENAI_MODEL`` writes the final answer from the system
   prompt + history + tool transcript, streamed token by token.
3. **Suggestions** — ``OPENAI_TOOL_MODEL`` proposes up to ``AGENT_SUGGESTIONS``
   follow-up prompts, grounded in the tool results and the answer (structured
   JSON output). Best-effort: a failure here never fails the turn.

:func:`run_chat_agent` is an async generator yielding, in order:
:class:`ToolCallEvent` per executed call, :class:`TokenEvent` per answer token,
:class:`SuggestionsEvent` (when any), and one final :class:`FinalEvent`. The
caller (``handler/chat.py``) forwards them to the WebSocket and to Kafka.

Langfuse layout (inside the caller's per-turn trace)::

    chat (trace, session = conversation id)
    ├── agent-tool     (agent)  output: {stop_reason, rounds, tool_calls[...]}
    │   ├── tool_decide:0  (generation)  the tool model's reasoning per round
    │   ├── tool:retrieval_cv  (tool)    duration + status; ERROR level on failure
    │   │   └── retrieval_cv   (retriever, from core/tools) query + full results
    │   └── tool_decide:1 ...
    ├── agent-summary  (agent)  output: the final answer
    │   └── summary        (generation)  streamed answer + usage
    └── suggestions    (generation)  follow-up prompts
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from core.llm import get_async_llm_client
from core.tools import FINISH_TOOL, TOOL_SPECS, execute_tool
from prompt import load_prompt
from schemas.chat import ChatTurn, ToolCallRecord
from services.observability import langfuse
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

# Friendly tool-failure message shown to the model (it may try another tool).
_TOOL_ERROR_CONTENT = "Sorry, this tool isn't accessible at the moment. Please try with another tool."


@dataclass(slots=True)
class ToolCallEvent:
    """One tool call just finished executing."""

    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass(slots=True)
class TokenEvent:
    """One streamed token of the final answer."""

    text: str


@dataclass(slots=True)
class SuggestionsEvent:
    """Follow-up prompts the user could send next."""

    suggestions: list[str]


@dataclass(slots=True)
class FinalEvent:
    """The finished turn: answer, executed tool calls, suggestions, aggregated usage."""

    answer: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    usage: dict[str, int] | None = None


def _result_summary(result: Any) -> Any:
    """Compact form of a tool result for persistence (full data lives in Langfuse)."""
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        hits = result["results"]
        return {
            "result_count": len(hits),
            "ids": [h.get("resume_id", h.get("job_id")) for h in hits if isinstance(h, dict)][:10],
        }
    return result


def _call_status(record: ToolCallRecord) -> dict[str, str]:
    """One line of the agent-tool span's status output."""
    if record.error:
        status = "error"
    elif isinstance(record.result, dict) and record.result.get("repeat_of_previous_call"):
        status = "repeat"
    else:
        status = "ok"
    return {"tool": record.tool, "status": status}


def _add_usage(total: dict[str, int], usage: Any) -> dict[str, int]:
    for key, value in (langfuse.usage_from_openai(usage) or {}).items():
        total[key] = total.get(key, 0) + value
    return total


# Strict JSON schema for the suggestion call's structured output.
_SUGGESTION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "followup_suggestions",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}},
            "required": ["suggestions"],
            "additionalProperties": False,
        },
    },
}


async def _suggest_followups(
    client: Any,
    model: str,
    conversation: list[dict[str, Any]],
    usage_total: dict[str, int],
) -> list[str]:
    """Follow-up prompts grounded in the tool results + answer. Never raises."""
    limit = settings.AGENT_SUGGESTIONS
    if limit <= 0:
        return []
    prompt = load_prompt("suggestion_system").format(max_suggestions=limit)
    suggest_input = [{"role": "system", "content": prompt}] + conversation
    try:
        with langfuse.log_generation("suggestions", model=model, input=suggest_input) as gen:
            response = await client.chat.completions.create(
                model=model,
                messages=suggest_input,
                response_format=_SUGGESTION_FORMAT,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            suggestions = [s.strip() for s in data.get("suggestions", []) if isinstance(s, str) and s.strip()]
            suggestions = suggestions[:limit]
            gen.update(output=suggestions, usage_details=langfuse.usage_from_openai(response.usage))
        _add_usage(usage_total, response.usage)
        return suggestions
    except Exception as exc:  # noqa: BLE001 — suggestions are optional decoration
        log.warning("suggestion step failed, continuing without: %s", exc)
        return []


async def run_chat_agent(
    message: str,
    history: Sequence[ChatTurn] = (),
) -> AsyncIterator[ToolCallEvent | TokenEvent | FinalEvent]:
    """Answer one user turn given the recent ``history`` (oldest first)."""
    client = get_async_llm_client()
    tool_model = settings.OPENAI_TOOL_MODEL or settings.OPENAI_MODEL
    base_messages = [{"role": t.role, "content": t.content} for t in history]
    base_messages.append({"role": "user", "content": message})

    # ---- Stage 1: tool loop -------------------------------------------------
    transcript: list[dict[str, Any]] = []      # assistant tool_calls + tool results
    records: list[ToolCallRecord] = []
    executed: dict[tuple[str, str], str] = {}  # (tool, canonical args) -> result JSON
    usage_total: dict[str, int] = {}
    stop_reason = "max_rounds"
    rounds = 0

    with langfuse.log_span(
        "agent-tool",
        as_type="agent",
        input={"message": message, "history_turns": len(history)},
        metadata={"model": tool_model, "max_rounds": settings.AGENT_MAX_TOOL_ROUNDS},
    ) as tool_agent:
        for round_no in range(settings.AGENT_MAX_TOOL_ROUNDS):
            rounds = round_no + 1
            decide_input = (
                [{"role": "system", "content": load_prompt("tool_system")}] + base_messages + transcript
            )
            with langfuse.log_generation(f"tool_decide:{round_no}", model=tool_model, input=decide_input) as gen:
                response = await client.chat.completions.create(
                    model=tool_model,
                    messages=decide_input,
                    tools=TOOL_SPECS,
                    tool_choice="required",
                )
                calls = response.choices[0].message.tool_calls or []
                gen.update(
                    output=[{"name": c.function.name, "arguments": c.function.arguments} for c in calls],
                    usage_details=langfuse.usage_from_openai(response.usage),
                )
            _add_usage(usage_total, response.usage)

            finish = any(c.function.name == FINISH_TOOL for c in calls)
            work = [c for c in calls if c.function.name != FINISH_TOOL]
            if not work:  # finished (or the model returned nothing to run)
                stop_reason = "finish_tool_calls" if finish else "no_tool_calls"
                break

            transcript.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in calls
                    ],
                }
            )
            # Every tool_call id needs a tool message, including a stray finish call.
            for c in calls:
                if c.function.name == FINISH_TOOL:
                    transcript.append({"role": "tool", "tool_call_id": c.id, "content": "done"})

            made_progress = False
            for c in work:
                try:
                    arguments = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                key = (c.function.name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))

                if key in executed:  # identical repeat: reuse the cached result, do not re-run
                    content = executed[key]
                    langfuse.log_observation(f"tool:{c.function.name}", as_type="tool", input=arguments,
                                             output={"repeat_of_previous_call": True},
                                             metadata={"status": "repeat"})
                    records.append(ToolCallRecord(tool=c.function.name, arguments=arguments,
                                                  result={"repeat_of_previous_call": True}))
                    yield ToolCallEvent(tool=c.function.name, arguments=arguments,
                                        result={"repeat_of_previous_call": True})
                else:
                    error: str | None = None
                    with langfuse.log_span(f"tool:{c.function.name}", as_type="tool", input=arguments) as tool_span:
                        try:
                            result = await execute_tool(c.function.name, arguments)
                            content = json.dumps(result, ensure_ascii=False, default=str)
                            tool_span.update(output=_result_summary(result), metadata={"status": "ok"})
                        except Exception as exc:  # noqa: BLE001 — the model gets a friendly retry hint
                            log.exception("tool %s failed: %s", c.function.name, exc)
                            error, result = f"{type(exc).__name__}: {exc}", None
                            content = _TOOL_ERROR_CONTENT
                            tool_span.update(level="ERROR", status_message=error, metadata={"status": "error"})
                    executed[key] = content
                    made_progress = made_progress or error is None
                    records.append(ToolCallRecord(tool=c.function.name, arguments=arguments,
                                                  result=_result_summary(result), error=error))
                    yield ToolCallEvent(tool=c.function.name, arguments=arguments,
                                        result=_result_summary(result), error=error)
                transcript.append({"role": "tool", "tool_call_id": c.id, "content": content})

            if finish:
                stop_reason = "finish_tool_calls"
                break
            if not made_progress:  # only repeats or failures this round
                stop_reason = "no_progress"
                break

        tool_agent.update(output={
            "stop_reason": stop_reason,
            "rounds": rounds,
            "tool_calls": [_call_status(r) for r in records],
        })

    # ---- Stage 2: summary ---------------------------------------------------
    summary_input = [{"role": "system", "content": load_prompt("system")}] + base_messages + transcript
    parts: list[str] = []
    with langfuse.log_span(
        "agent-summary",
        as_type="agent",
        input={"tool_results": len(records), "history_turns": len(history), "stop_reason": stop_reason},
        metadata={"model": settings.OPENAI_MODEL},
    ) as summary_agent:
        with langfuse.log_generation("summary", model=settings.OPENAI_MODEL, input=summary_input) as gen:
            stream = await client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=summary_input,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    parts.append(chunk.choices[0].delta.content)
                    yield TokenEvent(text=chunk.choices[0].delta.content)
                if getattr(chunk, "usage", None):
                    gen.update(usage_details=langfuse.usage_from_openai(chunk.usage))
                    _add_usage(usage_total, chunk.usage)
            gen.update(output="".join(parts))
        answer = "".join(parts)
        summary_agent.update(output=answer)

    # ---- Stage 3: follow-up suggestions -------------------------------------
    suggestions = await _suggest_followups(
        client,
        tool_model,
        base_messages + transcript + [{"role": "assistant", "content": answer}],
        usage_total,
    )
    if suggestions:
        yield SuggestionsEvent(suggestions=suggestions)

    yield FinalEvent(answer=answer, tool_calls=records, suggestions=suggestions, usage=usage_total or None)
