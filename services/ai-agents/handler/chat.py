"""Chat use-case: consume one ``chat.requests`` Kafka message end to end.

For every request from bot-agent this handler

1. runs the two-stage agent (``core.agent.run_chat_agent``),
2. streams its events to the conversation's WebSocket(s) as they happen
   (``tool_call`` status, answer tokens, ``done`` / ``error``), and
3. publishes the finished turn on ``chat.responses`` so bot-agent persists it.

The whole turn is one Langfuse trace (session = conversation id). Agent
failures are reported on both channels instead of re-raised, so the Kafka
consumer commits the offset and the request is not retried into a dead letter
after the user already saw the error.
"""

from __future__ import annotations

from pydantic import ValidationError

from core.agent import FinalEvent, SuggestionsEvent, TokenEvent, ToolCallEvent, run_chat_agent
from core.connections import manager
from schemas.chat import ChatRequestEvent, ChatResponseEvent
from services.message_broker import kafka
from services.observability import langfuse
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


async def handle_chat_request(msg: kafka.Message) -> None:
    """Kafka handler for ``chat.requests`` (registered in ``main.py``)."""
    try:
        request = ChatRequestEvent.model_validate(msg.value)
    except ValidationError as exc:
        # Malformed payload: nothing to answer — raise so it lands in the dead letter.
        log.error("invalid chat.requests payload at %s[%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
        raise

    cid = request.conversation_id
    response = ChatResponseEvent(conversation_id=cid, message_id=request.message_id)

    with langfuse.log_trace(
        "chat",
        conversation_id=cid,
        input={"message": request.message, "history_turns": len(request.history)},
        metadata={"message_id": request.message_id, "model": settings.OPENAI_MODEL},
    ) as trace:
        response.trace_id = trace.id
        try:
            async for event in run_chat_agent(request.message, request.history):
                if isinstance(event, ToolCallEvent):
                    await manager.send(cid, {"type": "tool_call", "tool": event.tool,
                                             "arguments": event.arguments})
                elif isinstance(event, TokenEvent):
                    await manager.send(cid, {"content": event.text})
                elif isinstance(event, SuggestionsEvent):
                    await manager.send(cid, {"type": "suggestions", "suggestions": event.suggestions})
                elif isinstance(event, FinalEvent):
                    response.answer = event.answer
                    response.tool_calls = event.tool_calls
                    response.suggestions = event.suggestions
                    response.usage = event.usage
        except Exception as exc:  # noqa: BLE001 — reported to the user + bot-agent below
            log.exception("agent failed for conversation %s: %s", cid, exc)
            response.error = f"{type(exc).__name__}: {exc}"
            trace.update(level="ERROR", status_message=response.error)

        trace.set_io(output=response.error or response.answer)

    if response.error:
        await manager.send(cid, {"type": "error", "message": "The assistant failed to answer. Please try again."})
    else:
        await manager.send(cid, {"type": "done", "conversation_id": cid})

    await kafka.produce(kafka.TOPIC_CHAT_RESPONSES, response.model_dump(mode="json"), key=cid)
    log.info("chat turn done: conversation=%s message=%s tools=%d error=%s",
             cid, request.message_id, len(response.tool_calls), response.error)
