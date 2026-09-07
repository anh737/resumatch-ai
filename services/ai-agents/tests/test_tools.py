"""core.tools — get_resume / get_job lookup logic and retrieval filters with a fake Qdrant adapter."""

import pytest

import core.agent as agent_module
import core.tools as tools
from services.vector_store import qdrant

UPLOADED = {
    1788710140077: {"filename": "BuiNgocAnh_ML_Engineer_2026.pdf", "stem": "buingocanh_ml_engineer_2026",
                    "upload_id": "u1", "uploaded_at": "2026-09-06T15:00:00+00:00"},
    1788710999999: {"filename": "Jane Doe CV.docx", "stem": "jane doe cv", "upload_id": "u2",
                    "uploaded_at": "2026-09-05T10:00:00+00:00"},
}
DATASET = {11584809}  # offline corpus resume: no `source`


def _point(rid, field="objective", source=None):
    payload = {"id": rid, "field": field, "category": "INFORMATION-TECHNOLOGY", "embedded_text": "x",
               "objective": f"objective of {rid}", "work_exp": [{"title": "ML Engineer"}], "information": {"education": []},
               "certification": [], "technical_skills": ["Python", "PyTorch"]}
    if source:
        payload["source"] = source
    return qdrant.Point(id=f"{rid}:{field}", payload=payload)


@pytest.fixture
def store(monkeypatch):
    calls: list[tuple] = []

    async def get_resume(rid):
        calls.append(("get_resume", rid))
        if rid in UPLOADED:
            return [_point(rid, f, UPLOADED[rid]) for f in ("objective", "technical_skills")]
        return [_point(rid)] if rid in DATASET else []

    async def find_resumes_by_source(*, filename=None, stem=None, limit=50):
        calls.append(("find", filename, stem))
        return [
            _point(rid, f, s)
            for rid, s in UPLOADED.items()
            if (filename and s["filename"] == filename) or (stem and s["stem"] == stem)
            for f in ("objective", "technical_skills")
        ]

    async def list_uploaded_resumes(**_):
        calls.append(("list",))
        return [_point(rid, source=s) for rid, s in UPLOADED.items()]

    monkeypatch.setattr(qdrant, "get_resume", get_resume)
    monkeypatch.setattr(qdrant, "find_resumes_by_source", find_resumes_by_source)
    monkeypatch.setattr(qdrant, "list_uploaded_resumes", list_uploaded_resumes)
    return calls


async def test_get_resume_by_id_returns_full_record(store):
    out = await tools.get_resume(resume_id="#11584809")  # string with '#' is coerced
    assert out["found"] is True and out["matched_by"] == "resume_id" and out["resume_id"] == 11584809
    assert out["source"] is None  # dataset resume
    assert set(out["record"]) == {"objective", "work_exp", "information", "certification", "technical_skills"}
    assert out["record"]["technical_skills"] == ["Python", "PyTorch"]


async def test_get_resume_exact_filename(store):
    out = await tools.get_resume(filename="BuiNgocAnh_ML_Engineer_2026.pdf")
    assert out["found"] and out["matched_by"] == "filename" and out["resume_id"] == 1788710140077
    assert out["source"]["filename"] == "BuiNgocAnh_ML_Engineer_2026.pdf" and out["sections_indexed"] == ["objective", "technical_skills"]


async def test_get_resume_matches_stem_without_extension_and_case(store):
    out = await tools.get_resume(filename="buingocanh_ml_ENGINEER_2026")
    assert out["found"] and out["matched_by"] == "filename" and out["resume_id"] == 1788710140077
    assert ("find", None, "buingocanh_ml_engineer_2026") in store


async def test_get_resume_fuzzy_match_lists_alternatives(store):
    out = await tools.get_resume(filename="BuiNgocAnh ML Engineer")  # spaces instead of underscores
    assert out["found"] and out["matched_by"] == "fuzzy" and out["resume_id"] == 1788710140077
    assert [c["filename"] for c in out["other_candidates"]] == ["Jane Doe CV.docx"]


async def test_get_resume_not_found_lists_uploaded_files(store):
    out = await tools.get_resume(filename="totally_different_name.pdf")
    assert out["found"] is False and out["resume_id"] is None
    assert [f["filename"] for f in out["uploaded_files"]] == ["BuiNgocAnh_ML_Engineer_2026.pdf", "Jane Doe CV.docx"]  # newest first
    assert out["closest_files"][0]["similarity"] < tools._FUZZY_MIN_RATIO
    assert "do not substitute" in out["note"]


async def test_get_resume_unknown_id_falls_back_to_filename(store):
    out = await tools.get_resume(resume_id=1, filename="Jane Doe CV.docx")
    assert out["found"] and out["resume_id"] == 1788710999999 and out["matched_by"] == "filename"


async def test_get_resume_requires_an_identifier(store):
    with pytest.raises(ValueError):
        await tools.get_resume()


async def test_execute_tool_dispatches_lookup_tools(store):
    out = await tools.execute_tool("get_resume", {"resume_id": 11584809})
    assert out["found"] is True
    with pytest.raises(tools.UnknownToolError):
        await tools.execute_tool("nope", {})


async def test_retrieval_cv_passes_filename_filter(monkeypatch):
    seen = {}

    async def embed_text(q):
        return [0.1, 0.2]

    async def search_resumes(vector, **kwargs):
        seen.update(kwargs)
        return [qdrant.SearchGroup(key=1788710140077, score=0.8, hits=[qdrant.SearchHit(id="p", score=0.8, payload=_point(1788710140077, source=UPLOADED[1788710140077]).payload)])]

    monkeypatch.setattr(tools, "embed_text", embed_text)
    monkeypatch.setattr(qdrant, "search_resumes", search_resumes)
    out = await tools.retrieval_cv("python engineer", filename="BuiNgocAnh_ML_Engineer_2026.pdf", top_k=3)
    assert seen["filename"] == "BuiNgocAnh_ML_Engineer_2026.pdf" and seen["limit"] == 3
    assert out["results"][0]["source"]["filename"] == "BuiNgocAnh_ML_Engineer_2026.pdf"


def test_result_summary_compacts_lookup_results():
    found = {"found": True, "matched_by": "filename", "resume_id": 5, "source": {"filename": "a.pdf"}, "record": {"objective": "x" * 5000}}
    assert agent_module._result_summary(found) == {"found": True, "matched_by": "filename", "resume_id": 5, "filename": "a.pdf"}
    missing = {"found": False, "filename": "z.pdf", "uploaded_files": [{"filename": "a.pdf"}, {"filename": "b.pdf"}]}
    assert agent_module._result_summary(missing) == {"found": False, "filename": "z.pdf", "uploaded_files": ["a.pdf", "b.pdf"]}


def test_filename_stem_rules():
    assert tools.filename_stem("BuiNgocAnh_ML_Engineer_2026.PDF") == "buingocanh_ml_engineer_2026"
    assert tools.filename_stem("  Data  Engineer JD.md") == "data engineer jd"
    assert tools.filename_stem("weird.name") == "weird.name"
