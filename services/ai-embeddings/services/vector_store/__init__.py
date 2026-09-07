"""vector_store — adapters for vector databases (one module per vendor).

Current: ``qdrant``. A new vendor (e.g. ``chromadb.py``) goes in this folder and
must expose the same public API so callers can swap it with one import::

    from services.vector_store import qdrant as vector_store

Contract every module here implements:
    search, search_grouped, search_resumes, search_jobs, get_resume, get_job,
    scroll, retrieve, count, build_filter, list_collections, collection_exists,
    ensure_collection, upsert_points, delete_points, ping, and a close_*
    function for shutdown; result types SearchHit, SearchGroup, Point.
"""
