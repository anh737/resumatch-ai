from fastapi import APIRouter, WebSocket

from handler.ws import chat_socket

router = APIRouter()


@router.websocket("/ws/chat/{conversation_id}")
async def ws_chat(websocket: WebSocket, conversation_id: str) -> None:
    await chat_socket(websocket, conversation_id)
