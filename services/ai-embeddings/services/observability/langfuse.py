"""Langfuse adapter — tracing (``log_*``) and feedback / data (``insert_*``).

Built on the Langfuse Python SDK v4 (OpenTelemetry based). Every function is a
safe no-op when tracing is disabled (``LANGFUSE_ENABLED=false`` or missing
keys), so callers never need to branch on configuration.

Tracing model (project_architecture.md §8): one **trace** per ``/chat`` request
(``log_trace``), with a nested **observation** per agent step — ``generation``
for LLM calls, ``tool`` / ``retriever`` for tool calls, ``span`` for anything
else. ``conversation_id`` becomes the Langfuse *session* so a whole conversation
can be replayed in the UI.

Typical use::

    with langfuse.log_trace("chat", conversation_id=cid, input=messages) as trace:
        with langfuse.log_generation("answer", model="gpt-4.1", input=messages) as gen:
            resp = await client.chat.completions.create(...)
            gen.update(output=resp.choices[0].message.content,
                       usage_details=langfuse.usage_from_openai(resp.usage))
        langfuse.log_tool_call("search_resumes", input={"query": q}, output=hits)
        trace.update(output=answer)
    trace_id = trace.id          # hand to the client for feedback later

    langfuse.insert_feedback(trace_id, positive=True)          # thumbs-up
    langfuse.insert_dataset_item("chat-evals", input=..., expected_output=...)

Blocking calls: ``log_*`` and ``insert_score``/``insert_feedback`` only enqueue
(non-blocking); ``insert_dataset_item``, ``insert_prompt``, ``get_prompt`` and
``auth_check`` do HTTP synchronously — from async code wrap them in
``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Literal, Mapping, Sequence

from langfuse import Langfuse
from langfuse import observe as _observe
from langfuse import propagate_attributes

from setting import settings
from utils.logger import get_logger

log = get_logger(__name__)

ObservationType = Literal["span", "generation", "embedding", "agent", "tool", "chain", "retriever", "evaluator", "guardrail"]
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]
ScoreDataType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN", "TEXT", "CORRECTION"]

#: Decorator re-export: ``@langfuse.observe(as_type="tool")`` traces a function call.
observe = _observe


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------
def enabled() -> bool:
    """True when tracing is switched on and both API keys are configured."""
    return bool(settings.LANGFUSE_ENABLED and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


@lru_cache(maxsize=1)
def get_langfuse() -> Langfuse | None:
    """Process-wide client, or ``None`` when tracing is disabled."""
    if not enabled():
        log.info("Langfuse tracing disabled (LANGFUSE_ENABLED=false or keys missing)")
        return None
    client = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        base_url=settings.LANGFUSE_HOST.rstrip("/"),
        environment=settings.LANGFUSE_ENVIRONMENT or None,
        release=settings.LANGFUSE_RELEASE or None,
        sample_rate=settings.LANGFUSE_SAMPLE_RATE,
    )
    log.info("Langfuse tracing enabled -> %s (env=%s)", settings.LANGFUSE_HOST, settings.LANGFUSE_ENVIRONMENT)
    return client


def auth_check() -> bool:
    """True if the configured keys are accepted by the server (used by /health)."""
    client = get_langfuse()
    if client is None:
        return False
    try:
        return bool(client.auth_check())
    except Exception as exc:  # noqa: BLE001 — health probes must never raise
        log.warning("Langfuse auth check failed: %s", exc)
        return False


def flush() -> None:
    """Send everything queued so far (call before the process exits or in tests)."""
    client = get_langfuse()
    if client is not None:
        client.flush()


def shutdown() -> None:
    """Flush and release the client (FastAPI shutdown hook)."""
    client = get_langfuse()
    if client is not None:
        client.shutdown()
    get_langfuse.cache_clear()


def current_trace_id() -> str | None:
    """Trace id of the observation currently open in this context, if any."""
    client = get_langfuse()
    return client.get_current_trace_id() if client is not None else None


def trace_url(trace_id: str | None) -> str | None:
    """Deep link to a trace in the Langfuse UI."""
    client = get_langfuse()
    if client is None or not trace_id:
        return None
    return client.get_trace_url(trace_id=trace_id)


# ---------------------------------------------------------------------------
# No-op stand-in used when tracing is disabled
# ---------------------------------------------------------------------------
class _NoopObservation:
    """Mimics ``LangfuseObservationWrapper`` so caller code never branches."""

    id: str | None = None
    trace_id: str | None = None

    def update(self, **_: Any) -> "_NoopObservation":
        return self

    def end(self, **_: Any) -> "_NoopObservation":
        return self

    def score(self, **_: Any) -> None:
        return None

    def score_trace(self, **_: Any) -> None:
        return None

    def set_trace_io(self, **_: Any) -> "_NoopObservation":
        return self

    def create_event(self, **_: Any) -> "_NoopObservation":
        return self

    def start_observation(self, **_: Any) -> "_NoopObservation":
        return self

    @contextlib.contextmanager
    def start_as_current_observation(self, **_: Any) -> Iterator["_NoopObservation"]:
        yield self


_NOOP = _NoopObservation()
Observation = Any  # LangfuseObservationWrapper | _NoopObservation


# ---------------------------------------------------------------------------
# log_* — tracing
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Trace:
    """Handle yielded by :func:`log_trace`."""

    id: str | None
    span: Observation

    def update(self, **kwargs: Any) -> "Trace":
        """Update the root span (``output=``, ``metadata=``, ``level=`` …)."""
        self.span.update(**kwargs)
        return self

    def set_io(self, *, input: Any = None, output: Any = None) -> "Trace":
        """Set the trace-level input/output shown in the trace list."""
        self.span.set_trace_io(input=input, output=output)
        return self

    @property
    def url(self) -> str | None:
        return trace_url(self.id)


@contextlib.contextmanager
def log_trace(
    name: str,
    *,
    conversation_id: str | None = None,
    user_id: str | None = None,
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    version: str | None = None,
) -> Iterator[Trace]:
    """Open the root observation of one request; nested ``log_*`` calls attach to it.

    ``conversation_id`` is stored as the Langfuse *session id*. An exception
    inside the block marks the trace ``ERROR`` and is re-raised.
    """
    client = get_langfuse()
    if client is None:
        yield Trace(id=None, span=_NOOP)
        return
    with propagate_attributes(
        session_id=conversation_id,
        user_id=user_id,
        tags=list(tags) if tags else None,
        metadata=dict(metadata) if metadata else None,
        version=version,
        trace_name=name,
    ):
        with client.start_as_current_observation(name=name, as_type="span", input=input, metadata=dict(metadata) if metadata else None) as span:
            if input is not None:
                span.set_trace_io(input=input)
            trace = Trace(id=client.get_current_trace_id(), span=span)
            try:
                yield trace
            except Exception as exc:
                span.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                raise


@contextlib.contextmanager
def log_span(
    name: str,
    *,
    as_type: ObservationType = "span",
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
    model: str | None = None,
    model_parameters: Mapping[str, Any] | None = None,
) -> Iterator[Observation]:
    """Nested observation for one step (tool, retriever, agent node …).

    Set the result with ``obs.update(output=...)`` before the block ends.
    """
    client = get_langfuse()
    if client is None:
        yield _NOOP
        return
    with client.start_as_current_observation(
        name=name,
        as_type=as_type,
        input=input,
        metadata=dict(metadata) if metadata else None,
        model=model,
        model_parameters=dict(model_parameters) if model_parameters else None,
    ) as obs:
        try:
            yield obs
        except Exception as exc:
            obs.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
            raise


@contextlib.contextmanager
def log_generation(
    name: str,
    *,
    model: str,
    input: Any = None,
    model_parameters: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[Observation]:
    """LLM call. Finish with ``gen.update(output=..., usage_details=usage_from_openai(resp.usage))``."""
    with log_span(name, as_type="generation", input=input, metadata=metadata, model=model, model_parameters=model_parameters) as gen:
        yield gen


def log_observation(
    name: str,
    *,
    as_type: ObservationType = "span",
    input: Any = None,
    output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    error: str | None = None,
    level: Level | None = None,
) -> Observation:
    """Record an already-completed step in one call (zero duration; child of the current trace)."""
    client = get_langfuse()
    if client is None:
        return _NOOP
    obs = client.start_observation(name=name, as_type=as_type, input=input, metadata=dict(metadata) if metadata else None)
    obs.update(output=output, level="ERROR" if error else level, status_message=error)
    obs.end()
    return obs


def log_tool_call(name: str, *, input: Any = None, output: Any = None, error: str | None = None, metadata: Mapping[str, Any] | None = None) -> Observation:
    """A finished tool call (e.g. from a LangGraph ``ToolNode`` result)."""
    return log_observation(name, as_type="tool", input=input, output=output, error=error, metadata=metadata)


def log_retrieval(name: str, *, query: Any, results: Any, metadata: Mapping[str, Any] | None = None) -> Observation:
    """A finished vector-search step."""
    return log_observation(name, as_type="retriever", input=query, output=results, metadata=metadata)


def log_event(
    name: str,
    *,
    input: Any = None,
    output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    level: Level = "DEFAULT",
    status_message: str | None = None,
) -> Observation:
    """Point-in-time marker on the current trace (e.g. ``"stream-stopped-by-user"``)."""
    client = get_langfuse()
    if client is None:
        return _NOOP
    return client.create_event(name=name, input=input, output=output, metadata=dict(metadata) if metadata else None, level=level, status_message=status_message)


def set_output(output: Any) -> None:
    """Set the output of the innermost open observation."""
    client = get_langfuse()
    if client is not None:
        client.update_current_span(output=output)


def set_trace_output(output: Any) -> None:
    """Set the trace-level output (what the trace list shows)."""
    client = get_langfuse()
    if client is not None:
        client.set_current_trace_io(output=output)


def usage_from_openai(usage: Any) -> dict[str, int] | None:
    """Map an OpenAI ``CompletionUsage`` (or dict) to Langfuse ``usage_details``."""
    if usage is None:
        return None
    get = usage.get if isinstance(usage, Mapping) else lambda k, d=None: getattr(usage, k, d)
    details = {"input": get("prompt_tokens"), "output": get("completion_tokens"), "total": get("total_tokens")}
    return {k: int(v) for k, v in details.items() if v is not None} or None


# ---------------------------------------------------------------------------
# insert_* — scores, feedback, datasets, prompts
# ---------------------------------------------------------------------------
def insert_score(
    *,
    name: str,
    value: float | str,
    trace_id: str | None = None,
    observation_id: str | None = None,
    session_id: str | None = None,
    data_type: ScoreDataType | None = None,
    comment: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Attach a score to a trace / observation / session (queued, non-blocking)."""
    client = get_langfuse()
    if client is None:
        return
    client.create_score(
        name=name,
        value=value,
        trace_id=trace_id,
        observation_id=observation_id,
        session_id=session_id,
        data_type=data_type,
        comment=comment,
        metadata=dict(metadata) if metadata else None,
    )


def insert_feedback(trace_id: str | None, *, positive: bool, comment: str | None = None, name: str = "user-feedback") -> None:
    """Thumbs up / down from the UI for the answer produced by ``trace_id``."""
    if not trace_id:
        return
    insert_score(name=name, value=1.0 if positive else 0.0, trace_id=trace_id, data_type="BOOLEAN", comment=comment)


def insert_dataset_item(
    dataset: str,
    *,
    input: Any,
    expected_output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    source_trace_id: str | None = None,
    item_id: str | None = None,
    ensure_dataset: bool = True,
    dataset_description: str | None = None,
) -> str | None:
    """Add an evaluation example to ``dataset`` (created on demand). Returns the item id. Blocking."""
    client = get_langfuse()
    if client is None:
        return None
    if ensure_dataset:
        client.create_dataset(name=dataset, description=dataset_description)  # upsert by name
    item = client.create_dataset_item(
        dataset_name=dataset,
        input=input,
        expected_output=expected_output,
        metadata=dict(metadata) if metadata else None,
        source_trace_id=source_trace_id,
        id=item_id,
    )
    return item.id


def insert_prompt(
    name: str,
    prompt: str | list[dict[str, Any]],
    *,
    labels: Sequence[str] = ("production",),
    type: Literal["text", "chat"] = "text",
    config: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    commit_message: str | None = None,
) -> int | None:
    """Store a new version of a managed prompt. Returns the version number. Blocking."""
    client = get_langfuse()
    if client is None:
        return None
    created = client.create_prompt(
        name=name,
        prompt=prompt,  # type: ignore[arg-type]
        labels=list(labels),
        tags=list(tags) if tags else None,
        type=type,
        config=dict(config) if config else None,
        commit_message=commit_message,
    )
    return created.version


def get_prompt(
    name: str,
    *,
    label: str = "production",
    type: Literal["text", "chat"] = "text",
    fallback: str | list[dict[str, Any]] | None = None,
    cache_ttl_seconds: int | None = None,
) -> Any:
    """Fetch a managed prompt (cached by the SDK). ``None`` when disabled or unavailable — fall back to ``prompt/``."""
    client = get_langfuse()
    if client is None:
        return None
    try:
        return client.get_prompt(name, label=label, type=type, fallback=fallback, cache_ttl_seconds=cache_ttl_seconds)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — prompt fetch must not break a request
        log.warning("Langfuse get_prompt(%s) failed: %s", name, exc)
        return None
