"""observability — adapters for LLM tracing / evaluation platforms (one module per vendor).

Current: ``langfuse`` — unlike ai-agents' write-side tracing adapter, bot-agent
only *reads* Langfuse (public REST API) so the admin portal can list recent
traces and open one trace's observations without exposing the secret keys to
the browser.

Contract every module here implements:
    enabled, public_url, trace_url, project_id, list_traces, get_trace, ping.
"""
