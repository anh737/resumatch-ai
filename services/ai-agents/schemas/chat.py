"""Chat contracts shared by the Kafka pipeline and the WebSocket stream.

Kafka (topic names in ``services/message_broker/kafka.py``):

* ``chat.requests``  — produced by **bot-agent**, consumed here. One user turn
  plus the most recent conversation history (:class:`ChatRequestEvent`).
* ``chat.responses`` — produced here after the agent finished, consumed by
  **bot-agent** which persists the assistant turn (:class:`ChatResponseEvent`).

WebSocket (``/ws/chat/{conversation_id}``) — server -> client JSON events, one
per frame:

    {"type": "tool_call", "tool": "retrieval_cv", "arguments": {...}}
    {"content": "answer token"}          # repeated while the summary streams
    {"type": "suggestions", "suggestions": ["...", ...]}   # follow-up prompts
    {"type": "done", "conversation_id": "..."}
    {"type": "error", "message": "..."}

The token / done / error shapes intentionally match the SSE contract the
front-end already parses (see services/front-end/README.md).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    """One prior message of the conversation, oldest first."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequestEvent(BaseModel):
    """Kafka ``chat.requests`` payload (bot-agent -> ai-agents)."""

    conversation_id: str
    message_id: str                      # id of the user message in bot-agent's DB
    message: str                         # the latest user turn
    history: list[ChatTurn] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    """One executed tool call, kept for persistence / observability."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None


class ChatResponseEvent(BaseModel):
    """Kafka ``chat.responses`` payload (ai-agents -> bot-agent)."""

    conversation_id: str
    message_id: str                      # echoes ChatRequestEvent.message_id
    answer: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)   # follow-up prompts for the UI
    usage: dict[str, int] | None = None
    trace_id: str | None = None          # Langfuse trace for this turn
    error: str | None = None             # set when the agent failed
