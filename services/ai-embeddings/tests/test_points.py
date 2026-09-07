"""core.points must reproduce the offline notebooks' schema bit-for-bit."""

import uuid

from core.points import (
    CV_EMBED_FIELDS,
    CV_PAYLOAD_INDEXES,
    JD_PAYLOAD_INDEXES,
    build_cv_points,
    build_jd_points,
    filename_stem,
    job_to_text,
    render_field,
    render_job,
    source_payload,
    truncate,
)

CV_RECORD = {
    "id": 10089434,
    "objective": "Versatile Systems Administrator.",
    "work_exp": [
        {
            "title": "IT Technician",
            "company": "Company Name",
            "start_date": "Aug 2007",
            "end_date": "Current",
            "location": "City State",
            "description": "Managing user accounts.",
        }
    ],
    "information": {
        "education": [{"degree": "BSc", "field": "IT", "institution": "FIU", "year": "2005"}],
        "languages": ["English"],
        "other": "Coursework in Programming.",
    },
    "certification": ["CompTIA Network+ - 2014"],
    "technical_skills": ["Linux", "Docker"],
}

JOB_RECORD = {
    "id": "upload_1725270000000",
    "job_title": "Data Engineer",
    "company": "Acme",
    "location": "Singapore",
    "salary": "",
    "description": "Build pipelines.",
    "requirements": ["Python", "SQL"],
    "responsibilities": ["Own ETL."],
    "benefits": ["Insurance"],
    "employment_type": "Full-time",
}


def test_cv_point_ids_and_payload():
    drafts = build_cv_points(CV_RECORD, category="INFORMATION-TECHNOLOGY")
    assert len(drafts) == 5  # every field is non-empty
    by_field = {d.payload["field"]: d for d in drafts}
    for field, draft in by_field.items():
        assert draft.id == str(uuid.uuid5(uuid.NAMESPACE_URL, f"cv:10089434:{field}"))
        assert draft.payload["id"] == 10089434
        assert draft.payload["category"] == "INFORMATION-TECHNOLOGY"
        assert draft.payload["embedded_text"] == draft.text
        # the whole record rides along on every point
        for f in CV_EMBED_FIELDS:
            assert draft.payload[f] == CV_RECORD[f]
    assert list(by_field["objective"].payload) == ["id", "field", "category", "embedded_text", *CV_EMBED_FIELDS]


def test_cv_render_field_shapes():
    assert render_field(CV_RECORD, "work_exp") == render_job(CV_RECORD["work_exp"][0])
    assert render_job(CV_RECORD["work_exp"][0]) == (
        "IT Technician | Company Name | Aug 2007 - Current | City State: Managing user accounts."
    )
    assert render_field(CV_RECORD, "information") == "BSc IT FIU 2005\nEnglish\nCoursework in Programming."
    assert render_field(CV_RECORD, "technical_skills") == "Linux; Docker"
    assert render_field({"id": 1}, "objective") is None


def test_jd_points_chunks_and_fields():
    drafts = build_jd_points(JOB_RECORD, chunks=["chunk zero", "chunk one"])
    chunk_points = [d for d in drafts if d.payload["type"] == "chunk"]
    field_points = [d for d in drafts if d.payload["type"] == "field"]

    assert [d.payload["chunk_index"] for d in chunk_points] == [0, 1]
    assert chunk_points[0].id == str(uuid.uuid5(uuid.NAMESPACE_URL, "jd_chunk:upload_1725270000000:0"))
    # benefits never becomes a field point; salary is empty -> description/requirements/responsibilities only
    assert {d.payload["field"] for d in field_points} == {"description", "requirements", "responsibilities"}
    req = next(d for d in field_points if d.payload["field"] == "requirements")
    assert req.id == str(uuid.uuid5(uuid.NAMESPACE_URL, "jd_field:upload_1725270000000:requirements"))
    assert req.payload["embedded_text"] == "Python; SQL"
    for d in drafts:
        assert d.payload["id"] == "upload_1725270000000"
        assert d.payload["benefits"] == ["Insurance"]


def test_job_to_text_label_order():
    text = job_to_text(JOB_RECORD)
    lines = text.split("\n")
    assert lines[0] == "Job Title: Data Engineer"
    assert "Salary:" not in text  # empty fields are omitted
    assert lines.index("Employment Type: Full-time") < lines.index("Description: Build pipelines.")
    assert "Requirements: Python; SQL" in lines


def test_truncate_returns_exact_embedded_text():
    text, n = truncate("short text")
    assert text == "short text" and n > 0
    long_text, n_long = truncate("word " * 20000)
    assert n_long == 8000  # settings.MAX_EMBED_TOKENS
    text_again, n_again = truncate(long_text)
    assert (text_again, n_again) == (long_text, n_long)  # truncation is idempotent


# ---------------------------------------------------------------------------
# Online uploads: the extra `source` block (absent from the offline corpus)
# ---------------------------------------------------------------------------
def test_filename_stem_is_case_insensitive_and_drops_known_extensions():
    assert filename_stem("BuiNgocAnh_ML_Engineer_2026.pdf") == "buingocanh_ml_engineer_2026"
    assert filename_stem("  Data Engineer  JD.DOCX ") == "data engineer jd"
    assert filename_stem("release.v2") == "release.v2"  # unknown suffix is part of the name
    assert filename_stem("C:\\Users\\me\\cv.pdf") == "cv"


def test_source_block_is_appended_after_notebook_keys():
    source = source_payload(filename="Jane CV.pdf", upload_id="u1", bucket="resumes", key="1/Jane CV.pdf",
                            content_type="application/pdf", uploaded_at="2026-09-06T15:00:00+00:00")
    assert source["stem"] == "jane cv" and source["ingested_at"]
    drafts = build_cv_points(CV_RECORD, category="INFORMATION-TECHNOLOGY", source=source)
    payload = drafts[0].payload
    assert list(payload) == ["id", "field", "category", "embedded_text", *CV_EMBED_FIELDS, "source"]
    assert payload["source"]["filename"] == "Jane CV.pdf" and payload["source"]["upload_id"] == "u1"
    # embedded_text is unaffected by the metadata
    assert payload["embedded_text"] == drafts[0].text

    jd = build_jd_points(JOB_RECORD, chunks=["c0"], source=source)
    assert all(d.payload["source"]["stem"] == "jane cv" for d in jd)
    assert jd[0].payload["type"] == "chunk" and jd[0].payload["job_title"] == "Data Engineer"


def test_offline_shape_has_no_source_and_indexes_cover_source_keys():
    assert "source" not in build_cv_points(CV_RECORD, category="X")[0].payload
    assert "source" not in build_jd_points(JOB_RECORD, chunks=["c0"])[0].payload
    for indexes in (CV_PAYLOAD_INDEXES, JD_PAYLOAD_INDEXES):
        assert indexes["source.filename"] == "keyword" and indexes["source.stem"] == "keyword"
