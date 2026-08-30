"""services.observability.langfuse — no-op when disabled; request shapes against an in-process mock server.

The real Langfuse secret key is not available in CI, so a tiny HTTP server
stands in for it and records what the SDK sends (OTLP traces, scores,
datasets, prompts). Assertions check that our function calls produce those
requests with the expected names.
"""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.observability import langfuse
from setting import settings

NOW = "2026-01-01T00:00:00.000Z"


class _Mock(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, bytes]] = []

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Mock.requests.append(("POST", self.path, body))
        p = self.path.split("?")[0]
        if p.startswith("/api/public/ingestion"):
            self._reply(207, {"successes": [], "errors": []})
        elif p.endswith("/datasets"):  # SDK v4 posts to /api/public/v2/datasets
            self._reply(200, {"id": "ds1", "name": json.loads(body)["name"], "description": None, "metadata": None,
                              "projectId": "p1", "createdAt": NOW, "updatedAt": NOW})
        elif p.endswith("/dataset-items"):
            b = json.loads(body)
            self._reply(200, {"id": b.get("id") or "item1", "status": "ACTIVE", "input": b.get("input"),
                              "expectedOutput": b.get("expectedOutput"), "metadata": b.get("metadata"),
                              "sourceTraceId": b.get("sourceTraceId"), "sourceObservationId": None,
                              "datasetId": "ds1", "datasetName": b["datasetName"], "createdAt": NOW, "updatedAt": NOW,
                              "mediaReferences": []})
        elif p.endswith("/prompts"):
            b = json.loads(body)
            self._reply(200, {"type": b.get("type", "text"), "name": b["name"], "version": 7, "prompt": b["prompt"],
                              "config": b.get("config") or {}, "labels": b.get("labels") or [], "tags": b.get("tags") or [],
                              "commitMessage": b.get("commitMessage")})
        else:
            self._reply(200, {})

    def do_GET(self):  # noqa: N802
        _Mock.requests.append(("GET", self.path, b""))
        if self.path.startswith("/api/public/projects"):
            self._reply(200, {"data": [{"id": "p1", "name": "resume", "metadata": {}, "retentionDays": None,
                                        "organization": {"id": "o1", "name": "AI-resume", "metadata": {}}}]})
        else:
            self._reply(200, {})

    def log_message(self, *_):  # silence
        pass


@pytest.fixture
def mock_server():
    _Mock.requests = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def configured(mock_server, monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(settings, "LANGFUSE_HOST", mock_server)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid.uuid4()}")  # unique: SDK caches clients per key
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-test")
    langfuse.get_langfuse.cache_clear()
    yield
    langfuse.shutdown()


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "")
    langfuse.get_langfuse.cache_clear()
    yield
    langfuse.get_langfuse.cache_clear()


def _bodies() -> bytes:
    return b"\n".join(b for _, _, b in _Mock.requests)


def _paths() -> list[str]:
    return [p.split("?")[0] for _, p, _ in _Mock.requests]


# --- disabled: everything is a no-op -----------------------------------------
def test_disabled_is_noop(disabled):
    assert langfuse.enabled() is False and langfuse.get_langfuse() is None
    with langfuse.log_trace("chat", conversation_id="c1", input="hi") as trace:
        assert trace.id is None and trace.url is None
        with langfuse.log_generation("gen", model="gpt-4.1", input="hi") as gen:
            gen.update(output="yo", usage_details={"input": 1})
        langfuse.log_tool_call("search", input={}, output=[])
        langfuse.log_event("evt")
        langfuse.set_output("x")
        trace.update(output="done").set_io(output="done")
    langfuse.insert_score(name="q", value=1)
    langfuse.insert_feedback(None, positive=True)
    assert langfuse.insert_dataset_item("ds", input={"a": 1}) is None
    assert langfuse.insert_prompt("p", "text") is None
    assert langfuse.get_prompt("p") is None
    assert langfuse.current_trace_id() is None and langfuse.auth_check() is False
    langfuse.flush()


def test_usage_from_openai():
    class U:
        prompt_tokens, completion_tokens, total_tokens = 10, 5, 15

    assert langfuse.usage_from_openai(U()) == {"input": 10, "output": 5, "total": 15}
    assert langfuse.usage_from_openai({"prompt_tokens": 3}) == {"input": 3}
    assert langfuse.usage_from_openai(None) is None


# --- enabled: requests reach the server ---------------------------------------
def test_log_trace_exports_spans_and_scores(configured):
    assert langfuse.enabled() and langfuse.auth_check() is True

    with langfuse.log_trace("chat-turn", conversation_id="conv-42", user_id="u-1", input={"q": "hello"},
                            tags=["smoke"], metadata={"k": "v"}) as trace:
        assert trace.id and len(trace.id) == 32
        assert langfuse.current_trace_id() == trace.id
        with langfuse.log_generation("answer-llm", model="gpt-4.1", input=[{"role": "user", "content": "hello"}],
                                     model_parameters={"temperature": 0}) as gen:
            gen.update(output="hi there", usage_details={"input": 3, "output": 2, "total": 5})
        with langfuse.log_span("agent-node", as_type="agent", input="x") as span:
            span.update(output="y")
        langfuse.log_tool_call("search_resumes", input={"query": "python"}, output=[{"id": 1}])
        langfuse.log_retrieval("qdrant", query="python", results=[1, 2])
        langfuse.log_event("stream-stopped", metadata={"reason": "user"})
        langfuse.set_output("final answer")
        trace.update(output="final answer").set_io(output="final answer")
        url = trace.url
    assert url and trace.id in url

    langfuse.insert_score(name="quality", value=0.9, trace_id=trace.id, comment="ok")
    langfuse.insert_feedback(trace.id, positive=True)
    langfuse.flush()

    paths = _paths()
    assert any("otel" in p for p in paths), paths
    blob = _bodies()
    for needle in [b"chat-turn", b"answer-llm", b"agent-node", b"search_resumes", b"qdrant", b"stream-stopped",
                   b"conv-42", b"u-1", b"gpt-4.1", b"quality", b"user-feedback"]:
        assert needle in blob, f"{needle!r} not sent; paths={paths}"


def test_trace_marks_error_and_reraises(configured):
    with pytest.raises(ValueError):
        with langfuse.log_trace("boom") as trace:
            raise ValueError("bad input")
    langfuse.flush()
    assert b"ValueError: bad input" in _bodies()


def test_insert_dataset_item_and_prompt(configured):
    item_id = langfuse.insert_dataset_item("chat-evals", input={"q": "hi"}, expected_output="hello",
                                           metadata={"src": "test"}, item_id="it-1")
    assert item_id == "it-1"
    posts = [(p.split("?")[0], json.loads(b)) for m, p, b in _Mock.requests if m == "POST" and "/dataset" in p]
    ds_post = next(b for p, b in posts if p.endswith("/datasets"))
    assert ds_post["name"] == "chat-evals"
    item_post = next(b for p, b in posts if p.endswith("/dataset-items"))
    assert item_post["datasetName"] == "chat-evals" and item_post["expectedOutput"] == "hello"

    version = langfuse.insert_prompt("system", "You are helpful.", labels=["production", "latest"], config={"model": "gpt-4.1"})
    assert version == 7
    prompt_post = next(json.loads(b) for m, p, b in _Mock.requests if p.startswith("/api/public/v2/prompts"))
    assert prompt_post["name"] == "system" and prompt_post["prompt"] == "You are helpful." and "production" in prompt_post["labels"]
