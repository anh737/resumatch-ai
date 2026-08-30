"""core.connections + the /ws/chat route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.connections import ConnectionManager
from router import api_router


class FakeSocket:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(payload)


async def test_send_reaches_registered_sockets_only():
    manager = ConnectionManager()
    ws_a, ws_b, dead = FakeSocket(), FakeSocket(), FakeSocket(fail=True)
    await manager.register("c1", ws_a)
    await manager.register("c1", dead)
    await manager.register("c2", ws_b)

    delivered = await manager.send("c1", {"content": "hi"})

    assert delivered == 1 and ws_a.sent == [{"content": "hi"}] and ws_b.sent == []
    assert await manager.send("unknown", {"x": 1}) == 0

    await manager.unregister("c1", ws_a)
    await manager.unregister("c1", dead)
    assert await manager.send("c1", {"x": 1}) == 0


def test_ws_route_accepts_connection():
    app = FastAPI()  # no lifespan: this smoke test must not need Kafka
    app.include_router(api_router)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/conv-1") as ws:
        ws.send_text("ping")  # client frames are ignored; connection stays open
