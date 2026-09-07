"""core.structure — normalization helpers must match preprocess_jd.ipynb."""

import json

import pytest

from core import structure


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, ""),
        ("", ""),
        ("fulltime", "Full-time"),
        ("Full-time", "Full-time"),
        ("PART TIME", "Part-time"),
        ("fulltime, contract", "Full-time, Contract"),
        ("Contract/Temporary", "Contract, Temporary"),
        ("fulltime,fulltime", "Full-time"),
        ("Freelance", "Freelance"),  # unknown values pass through
    ],
)
def test_norm_employment_type(raw, expected):
    assert structure.norm_employment_type(raw) == expected


async def test_structure_jd_fills_notebook_defaults(monkeypatch):
    async def fake_extract(name, schema, system_prompt, text, *, attempts=5):
        assert name == "jd_extraction" and text == "raw posting"
        return {"job_title": "Data Engineer", "company": None, "requirements": ["Python"], "employment_type": "fulltime"}

    monkeypatch.setattr(structure, "_extract", fake_extract)
    job = await structure.structure_jd("raw posting", job_id="upload_1")
    # exact key order + defaults of the notebook's normalize()
    assert list(job) == ["id", "job_title", "company", "location", "salary", "description",
                         "requirements", "responsibilities", "benefits", "employment_type"]
    assert job["company"] == "" and job["responsibilities"] == [] and job["employment_type"] == "Full-time"
    json.dumps(job)  # payload must be JSON-serializable for Qdrant


def test_schemas_are_strict_json_schema():
    for schema in (structure.CV_SCHEMA, structure.JD_SCHEMA):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
