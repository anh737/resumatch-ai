"""observability — adapters for LLM tracing / evaluation platforms (one module per vendor).

Current: ``langfuse``. A new vendor (e.g. ``langsmith.py``) goes in this folder
and must expose the same public API::

    from services.observability import langfuse as tracing

Contract every module here implements:
    log_trace, log_span, log_generation, log_tool_call, log_retrieval, log_event,
    set_output, set_trace_output, usage_from_openai, insert_score, insert_feedback,
    insert_dataset_item, insert_prompt, get_prompt, flush, shutdown, auth_check,
    enabled, current_trace_id, trace_url; type Trace.
"""
