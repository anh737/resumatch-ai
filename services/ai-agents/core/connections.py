"""In-memory registry of WebSocket connections, keyed by conversation id.

The front-end opens ``/ws/chat/{conversation_id}`` before posting its message
to bot-agent; the Kafka handler streams agent events to whoever is connected.
A conversation with no open socket is not an error — the turn still completes
and is persisted by bot-agent, the client just misses the live stream.

Single-process only (matches the one-uvicorn deployment). A multi-replica
setup would need a shared channel (e.g. Redis pub/sub) behind this interface.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from utils.logger import get_logger

log = get_logger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, conversation_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._sockets.setdefault(conversation_id, set()).add(websocket)
        log.debug("ws registered for conversation %s", conversation_id)

    async def unregister(self, conversation_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._sockets.get(conversation_id)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    del self._sockets[conversation_id]
        log.debug("ws unregistered for conversation %s", conversation_id)

    async def send(self, conversation_id: str, payload: dict[str, Any]) -> int:
        """Send one JSON event to every socket of the conversation.

        Returns the number of sockets reached; dead sockets are dropped silently
        (their reader task unregisters them on disconnect).
        """
        async with self._lock:
            sockets = list(self._sockets.get(conversation_id, ()))
        delivered = 0
        for ws in sockets:
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception:  # noqa: BLE001 — a closing socket must not break the stream
                log.debug("ws send failed for conversation %s (socket closing?)", conversation_id)
        return delivered


manager = ConnectionManager()
