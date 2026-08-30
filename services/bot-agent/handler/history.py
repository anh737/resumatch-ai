"""History use-cases: read conversations and their messages for the front-end."""

from fastapi import HTTPException

from schemas.chat import ConversationOut, MessageOut
from services.database import postgres


async def get_conversations() -> list[ConversationOut]:
    return [ConversationOut(**vars(c)) for c in await postgres.list_conversations()]


async def get_messages(conversation_id: str) -> list[MessageOut]:
    messages = await postgres.list_messages(conversation_id)
    if not messages:
        # Distinguish "empty" from "unknown" so the UI can drop stale ids.
        if not any(c.id == conversation_id for c in await postgres.list_conversations()):
            raise HTTPException(status_code=404, detail="conversation not found")
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, tool_calls=m.tool_calls,
                   suggestions=m.suggestions, error=m.error, created_at=m.created_at)
        for m in messages
    ]


async def remove_conversation(conversation_id: str) -> None:
    if not await postgres.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
