"""object_store — adapters for blob/object storage (one module per vendor).

Current: ``minio``. A new vendor (e.g. ``s3.py``) goes in this folder and must
expose the same public API::

    from services.object_store import minio as object_store

Contract every module here implements:
    ensure_bucket, put_object, get_object, remove_object, ping, and a close_*
    function for shutdown.
"""
