"""message_broker — adapters for event/message brokers (one module per vendor).

Current: ``kafka``. A new vendor (e.g. ``rabbitmq.py``, ``redis_streams.py``)
goes in this folder and must expose the same public API::

    from services.message_broker import kafka as broker

Contract every module here implements:
    produce, produce_many, consume, start_consumer, stop_consumer, stop_producer,
    ensure_topics, list_topics, ping; types Message, Delivery, Handler; the
    TOPIC_* constants and topic_name().
"""
