"""WebSocket use-case: keep one client subscribed to a conversation's stream.

The socket is receive-only for the client (messages arrive via bot-agent /
Kafka); anything the client sends is ignored except that the read loop doubles
as the disconnect detector.
"""

from __future__ import annotations

from fastapi import WebSocket, WebSocketDisconnect

from core.connections import manager
from utils.logger import get_logger

log = get_logger(__name__)


async def chat_socket(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    await manager.register(conversation_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # ignored; raises on disconnect
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(conversation_id, websocket)
