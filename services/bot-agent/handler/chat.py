"""Chat use-cases: accept a turn from the front-end, persist the agent's reply.

Submit path (HTTP): persist the user message, load the most recent
``CHAT_HISTORY_TURNS`` pairs *before* it, and forward everything to ai-agents
on the ``chat.requests`` Kafka topic. The answer itself reaches the browser
over ai-agents' WebSocket; bot-agent only queues and records.

Response path (Kafka): ``chat.responses`` messages are persisted as assistant
turns. The row id is derived from the user message id (``<id>:reply``) so a
redelivered message (at-least-once consumer) is a no-op.
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError

from schemas.chat import ChatAccepted, ChatRequest, ChatRequestEvent, ChatResponseEvent
from services.database import postgres
from services.message_broker import kafka
from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)


def _title_from(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line[:60] + ("…" if len(one_line) > 60 else "")


async def submit_chat(request: ChatRequest) -> ChatAccepted:
    conversation_id = request.conversation_id or str(uuid.uuid4())
    message = request.message.strip()
    message_id = str(uuid.uuid4())

    await postgres.ensure_conversation(conversation_id, title=_title_from(message))
    # History is read before the new message is stored, so it holds the
    # previous CHAT_HISTORY_TURNS pairs and never the message itself.
    history = await postgres.recent_turns(conversation_id, settings.CHAT_HISTORY_TURNS)
    await postgres.insert_user_message(message_id, conversation_id, message)

    event = ChatRequestEvent(
        conversation_id=conversation_id,
        message_id=message_id,
        message=message,
        history=history,
    )
    await kafka.produce(kafka.TOPIC_CHAT_REQUESTS, event.model_dump(mode="json"), key=conversation_id)
    log.info("queued chat turn: conversation=%s message=%s history=%d", conversation_id, message_id, len(history))
    return ChatAccepted(conversation_id=conversation_id, message_id=message_id)


async def handle_chat_response(msg: kafka.Message) -> None:
    """Kafka handler for ``chat.responses`` (registered in ``main.py``)."""
    try:
        response = ChatResponseEvent.model_validate(msg.value)
    except ValidationError as exc:
        log.error("invalid chat.responses payload at %s[%s]@%s: %s", msg.topic, msg.partition, msg.offset, exc)
        raise

    await postgres.ensure_conversation(response.conversation_id)
    inserted = await postgres.insert_assistant_message(
        f"{response.message_id}:reply",
        response.conversation_id,
        response.answer,
        tool_calls=[t.model_dump(mode="json") for t in response.tool_calls] or None,
        suggestions=response.suggestions or None,
        error=response.error,
    )
    if not inserted:
        log.info("duplicate chat.responses for message %s ignored", response.message_id)
        return
    log.info("stored assistant turn: conversation=%s message=%s tools=%d error=%s",
             response.conversation_id, response.message_id, len(response.tool_calls), response.error)
