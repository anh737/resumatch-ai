"""Chat contracts: the HTTP API for the front-end and the Kafka payloads.

The Kafka models mirror ``ai-agents/schemas/chat.py`` — the two services share
the wire format of ``chat.requests`` / ``chat.responses``.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# HTTP API (front-end -> bot-agent)
# ---------------------------------------------------------------------------
class ChatMessagePayload(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """One user turn. ``message`` is preferred; a legacy ``messages`` list is
    accepted (the last user entry is used) so older clients keep working."""

    conversation_id: str | None = None
    message: str | None = None
    messages: list[ChatMessagePayload] | None = None

    @model_validator(mode="after")
    def _resolve_message(self) -> "ChatRequest":
        if not self.message:
            for entry in reversed(self.messages or []):
                if entry.role == "user" and entry.content.strip():
                    self.message = entry.content
                    break
        if not (self.message and self.message.strip()):
            raise ValueError("request contains no user message")
        return self


class ChatAccepted(BaseModel):
    """The turn was queued for ai-agents; the answer arrives on the WebSocket."""

    conversation_id: str
    message_id: str
    status: Literal["queued"] = "queued"


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    suggestions: list[str] | None = None
    error: str | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Kafka payloads (shared wire format with ai-agents)
# ---------------------------------------------------------------------------
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequestEvent(BaseModel):
    """``chat.requests`` — produced here, consumed by ai-agents."""

    conversation_id: str
    message_id: str
    message: str
    history: list[ChatTurn] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None


class ChatResponseEvent(BaseModel):
    """``chat.responses`` — produced by ai-agents, consumed here."""

    conversation_id: str
    message_id: str
    answer: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    usage: dict[str, int] | None = None
    trace_id: str | None = None
    error: str | None = None
