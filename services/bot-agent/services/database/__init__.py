"""database — adapters for relational databases (one module per vendor).

Current: ``postgres``. Contract every module here implements:
    init_schema, ensure_conversation, insert_user_message, insert_assistant_message,
    recent_turns, list_messages, list_conversations, delete_conversation,
    insert_upload, update_upload_status, reset_upload, list_uploads, get_upload, upload_stats,
    ping, close_pool; result types MessageRecord, ConversationRecord, UploadRecord,
    UploadStatsRow.
"""
