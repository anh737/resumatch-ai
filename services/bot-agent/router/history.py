from fastapi import APIRouter

from handler.history import get_conversations, get_messages, remove_conversation
from schemas.chat import ConversationOut, MessageOut

router = APIRouter(tags=["history"])


@router.get("/conversations", response_model=list[ConversationOut])
async def conversations() -> list[ConversationOut]:
    return await get_conversations()


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def messages(conversation_id: str) -> list[MessageOut]:
    return await get_messages(conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete(conversation_id: str) -> None:
    await remove_conversation(conversation_id)
