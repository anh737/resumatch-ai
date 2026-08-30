"""database — adapters for relational databases (one module per vendor).

Current: ``postgres``. Contract every module here implements:
    init_schema, ensure_conversation, insert_user_message, insert_assistant_message,
    recent_turns, list_messages, list_conversations, ping, close_pool;
    result type MessageRecord.
"""
