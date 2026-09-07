# ai-agents — AI Processing

How a chat turn is processed by the LLM agent: the three stages, the retrieval
tools, prompting, memory, observability, and failure handling. The system
around the agent (topology, Kafka/WS contracts, data layer) is documented in
[project_architecture.md](project_architecture.md).

All AI logic lives in `core/`, built directly on the async OpenAI SDK — no
agent framework. The design principle: **separate deciding what evidence to
fetch from writing the answer**, so tool selection can run on a cheaper model
and the answer model always sees complete evidence.

---

## 1. The pipeline at a glance

```
chat.requests ──▶ STAGE 1 · agent-tool ──▶ STAGE 2 · agent-summary ──▶ STAGE 3 · suggestions
(message +        tool_decide ⇄ tools       gpt-4.1 streams the         ≤3 follow-up prompts,
 10-turn           ≤ 3 rounds, forced        answer from the full        strict JSON, best-effort
 history)          tool choice               tool transcript
                        │                         │                          │
                        ▼                         ▼                          ▼
                  WS {"type":"tool_call"}   WS {"content": token}      WS {"type":"suggestions"}
                                                       └──────▶ WS {"type":"done"} + Kafka chat.responses
```

Entry point: `core/agent.py :: run_chat_agent(message, history)` — an async
generator yielding `ToolCallEvent`, `TokenEvent`, `SuggestionsEvent`, and one
final `FinalEvent`. The Kafka handler (`handler/chat.py`) fans the events out
to the conversation's WebSocket(s) and publishes the finished turn on
`chat.responses` (answer, tool calls, suggestions, aggregated usage, trace id).

| Stage | Model | Output |
|---|---|---|
| agent-tool | `OPENAI_TOOL_MODEL` (falls back to `OPENAI_MODEL`) | tool transcript + executed-call records |
| agent-summary | `OPENAI_MODEL` (gpt-4.1) | the answer, streamed token by token |
| suggestions | `OPENAI_TOOL_MODEL` | ≤ `AGENT_SUGGESTIONS` follow-up prompts |

---

## 2. Stage 1 — agent-tool (evidence gathering)

The tool model is bound to exactly five functions (`core/tools.py ::
TOOL_SPECS`) with **`tool_choice="required"`** — it must call something every
round; the escape hatch is an explicit no-op tool:

- `retrieval_cv(query, top_k, section?, category?, filename?)` — semantic search over resumes
- `get_resume(resume_id? | filename?)` / `get_job(job_id? | filename?)` — one document in full by id or by the file name it was uploaded with (a file name is an identifier, never a search phrase)
- `retrieval_jd(query, top_k, company?, employment_type?)` — semantic search over job descriptions
- `finish_tool_calls()` — REQUIRED terminator, called alone when evidence is
  sufficient, or when no tool fits (small talk goes straight to the summary)

Its system prompt (`prompt/tool_system.md`) forbids answering the user,
requires self-contained English search phrases with pronouns resolved from the
conversation, allows both tools in one round for candidate↔job matching, and
permits at most one reformulated retry per idea.

### The loop

Each round appends to a growing **tool transcript** — the assistant's
`tool_calls` message plus one tool-result message per call id, exactly as the
OpenAI API requires — so the next round (and later the summary) sees everything
already retrieved. Calls in one round execute sequentially; results are
serialized as JSON into the transcript.

Four guards bound the loop; the reason the loop ended is recorded as
`stop_reason` (visible on the Langfuse `agent-tool` span):

| Guard | Mechanism | `stop_reason` |
|---|---|---|
| Model declares done | `finish_tool_calls` requested | `finish_tool_calls` |
| Model returns no calls | defensive; shouldn't happen with `required` | `no_tool_calls` |
| Identical repeat | dedupe key `(tool, canonical-JSON args)`; the cached result is replayed instead of re-querying and the round counts as no progress | `no_progress` |
| Failed round | no successful *new* call this round | `no_progress` |
| Hard cap | `AGENT_MAX_TOOL_ROUNDS` (default 3) rounds used | `max_rounds` |

A tool exception never propagates to the model: it receives the friendly
string *"Sorry, this tool isn't accessible at the moment. Please try with
another tool."* as the tool result, while the real error is recorded on the
call's `ToolCallRecord.error` and on its Langfuse span (ERROR level).

---

## 3. Retrieval — how a tool call becomes evidence

1. **Embed** the query with `core/llm.py :: embed_text` using
   `OPENAI_EMBEDDING_MODEL` (`text-embedding-3-small`, 1536-d). This is pinned
   to the model the offline `pre-processing/` pipeline used — with a different
   model, vector search is meaningless.
2. **Group-by search** (`services/vector_store/qdrant.py`): because one
   resume/job spans several points, `search_resumes` / `search_jobs` use
   Qdrant's group-by query so each hit is one distinct entity —
   a candidate with its best-matching *sections* (`information`, `objective`,
   `work_exp`, `technical_skills`, `certification`), or a job with its best
   points. Optional arguments become payload filters (section, category,
   company, employment_type).
3. **Structured JD records**: job search matches both `chunk` and `field`
   points; when a `field` point hits, the tool lifts the full record
   (job_title, company, location, salary, requirements[], responsibilities[],
   benefits[], employment_type) into the result.
4. **Shape for the LLM**: per-section snippets truncated to 600 chars, scores
   rounded, ids kept so the answer can cite `resume #id` / `job #id`.
   The *full* results go to Langfuse (RETRIEVER span); only a compact
   `{result_count, ids}` summary is persisted with the turn
   (`_result_summary` in `core/agent.py`).

---

## 4. Stage 2 — agent-summary (answer writing)

`OPENAI_MODEL` receives:

```
[ system: prompt/system.md ]
[ history: last 10 user+assistant pairs ]
[ user: the new message ]
[ the entire tool transcript: assistant tool_calls + tool results ]
```

and streams the reply token by token (`stream=True`,
`stream_options.include_usage` for exact token counts). The system prompt
enforces grounding:

- every claim must come from the retrieved results — never invent candidates,
  jobs, companies, or numbers; admit empty/off-topic results and suggest a
  rephrase;
- cite candidates as `resume #<id>` and jobs by title + company (plus
  `job #<id>`);
- multiple matches → short ranked list, best first, one or two lines each;
- answer in the user's language; scores are for judgement, mentioned only if
  asked; no-lookup questions (greetings, capability questions) answered
  directly.

Each token is yielded as a `TokenEvent` and forwarded as a WS
`{"content": token}` frame as it arrives.

---

## 5. Stage 3 — suggestions (follow-up prompts)

After the answer, one non-streamed call on the tool model sees the
conversation, the tool transcript, **and the finished answer**, and returns up
to `AGENT_SUGGESTIONS` (default 3) follow-up prompts through OpenAI's strict
`json_schema` structured output (`{"suggestions": [str]}`) — parsing cannot
fail on format. Rules (`prompt/suggestion_system.md`):

- phrased as a message the USER would send, imperative, under 90 characters,
  in the user's language;
- grounded in entities actually retrieved (drill into a result, compare
  candidate↔job, widen/narrow the search) — never invented ones;
- fewer or zero suggestions is fine; no small talk, no duplicates of what was
  already asked.

The stage is **best-effort**: any failure is logged and skipped; the turn
completes normally. Suggestions are sent as a WS `suggestions` frame (rendered
as clickable chips on the latest answer), persisted on the assistant row in
bot-agent's Postgres, and included in `chat.responses`.

---

## 6. Conversational memory

Memory is server-side and windowed. bot-agent forwards the last
`CHAT_HISTORY_TURNS` (10) user+assistant pairs with every request — read from
Postgres *before* the new message is stored, with empty/failed turns filtered
out — so ai-agents stays stateless per turn. Both the tool decider and the
summary see this history, which is what lets a follow-up like *"find jobs for
the first candidate you mentioned"* resolve to a concrete resume id from the
previous turn. Token usage is aggregated across all stages into one
`{input, output, total}` per turn.

---

## 7. Observability — one Langfuse trace per turn

`handler/chat.py` opens a trace per turn with the conversation id as the
Langfuse *session*, so a conversation replays as one thread. Stages are typed
observations with explicit status fields:

```
chat  (trace · session = conversation_id)
├── agent-tool      (AGENT)   output: { stop_reason, rounds,
│   │                                   tool_calls: [{tool, status: ok|error|repeat}] }
│   ├── tool_decide:<n>   (GENERATION)  per-round reasoning: input, chosen calls, tokens
│   └── tool:<name>       (TOOL)        duration + status; ERROR level with the exception
│       └── <name>        (RETRIEVER)   the query + full Qdrant results
├── agent-summary   (AGENT)   output: the final answer
│   └── summary           (GENERATION)  streamed answer + exact token usage
└── suggestions     (GENERATION)  follow-up prompts + usage
```

The adapter (`services/observability/langfuse.py`) is a safe no-op when keys
are absent, so the same code runs in tests (in-process mock server,
`tests/test_langfuse.py`, `tests/test_agent_tracing.py`) and in production.
Verify live keys with `GET /health?probe=true` → `"langfuse": true`.

---

## 8. Failure handling

| Failure | Behaviour |
|---|---|
| Retrieval tool throws (Qdrant down, bad args) | model gets the friendly retry hint; error recorded on the tool span and in persisted `tool_calls`; loop stops if the round made no progress; summary still runs on whatever evidence exists |
| Any stage throws | WS `error` frame; `chat.responses` carries the error; Kafka offset commits (no retry storm) |
| Suggestion stage throws | logged and skipped; turn completes normally |
| Malformed `chat.requests` payload | raised → dead-letter topic with source topic + error |
| Kafka redelivery (at-least-once) | bot-agent's idempotent insert (`{message_id}:reply`) makes duplicates a logged no-op |
| No WebSocket listener | turn completes and persists; the stream is simply unobserved |

---

## 9. Configuration knobs

| Setting | Default | Effect |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4.1` | summary (answer) model |
| `OPENAI_TOOL_MODEL` | empty → `OPENAI_MODEL` | tool-decide + suggestions model (set a cheaper one to cut cost) |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | must match the offline pipeline |
| `AGENT_MAX_TOOL_ROUNDS` | 3 | hard cap on tool-decide rounds |
| `AGENT_RETRIEVAL_TOP_K` | 5 | default hits per retrieval call |
| `AGENT_SUGGESTIONS` | 3 | follow-up prompts per turn; 0 disables stage 3 |
| `CHAT_HISTORY_TURNS` (bot-agent) | 10 | user+assistant pairs forwarded as memory |

Prompts live in `prompt/` (`tool_system.md`, `system.md`,
`suggestion_system.md`) and are loaded at runtime via `prompt.load_prompt()` —
never hard-coded in Python.
