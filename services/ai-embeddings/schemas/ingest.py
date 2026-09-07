"""Wire contracts of the ingestion pipeline (Kafka events).

bot-agent stores an uploaded file in MinIO and produces ``resume.uploaded`` /
``job.uploaded`` with the object's location; ai-embeddings answers on
``resume.processed`` / ``job.processed`` — first with ``status="processing"``
when it picks the event up, then with the terminal ``done``/``failed``.
Kafka message keys are the ``upload_id`` so events of one upload stay ordered.
"""

from typing import Literal

from pydantic import BaseModel


class ResumeUploadedEvent(BaseModel):
    upload_id: str            # bot-agent's ingestion_uploads row id
    resume_id: int            # payload "id" in cv_information_technology (integer!)
    bucket: str               # MinIO bucket holding the raw file
    key: str                  # MinIO object key
    filename: str             # original filename (drives text extraction)
    content_type: str | None = None
    category: str | None = None  # defaults to settings.CV_DEFAULT_CATEGORY
    uploaded_at: str | None = None  # ISO timestamp of the admin upload (bot-agent row)


class JobUploadedEvent(BaseModel):
    upload_id: str
    job_id: str               # payload "id" in jd_jobs (string, e.g. "upload_...")
    bucket: str
    key: str
    filename: str
    content_type: str | None = None
    uploaded_at: str | None = None


class IngestProcessedEvent(BaseModel):
    upload_id: str
    kind: Literal["cv", "jd"]
    document_id: str          # resume_id / job_id as string
    status: Literal["processing", "done", "failed"]
    points: int | None = None    # points upserted (terminal "done" only)
    error: str | None = None     # "ExcType: message" (terminal "failed" only)
    trace_id: str | None = None  # Langfuse trace of this ingestion
