import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fd, DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["ASCENT_DB"] = DB_PATH

from fastapi.testclient import TestClient
from app.main import app

_cm = TestClient(app, headers={"X-Ascent-Client": "1"})
client = _cm.__enter__()
client.post("/api/auth/setup", json={"passphrase": "test passphrase 123"})

import pytest


@pytest.fixture(autouse=True)
def _db_env():
    os.environ["ASCENT_DB"] = DB_PATH

JD = ("SDE Intern at Elip. We need Python, FastAPI, SQL and PostgreSQL. "
      "React and Docker are a plus. Machine learning knowledge preferred. "
      "Full-time hybrid role, 4-6 LPA, Bachelor's degree in Computer Science.")

JD_OTHER = ("Data Analyst intern wanted. Strong SQL and Excel required. "
            "Python pandas experience is a plus. Remote, stipend provided. "
            "Bachelor's degree in any discipline. Apply with resume.")


def _seed_profile():
    client.put("/api/profile/fields", json={"fields": {
        "name": "Test Candidate", "headline": "CS student",
        "summary": "Python developer"}})
    client.post("/api/profile/skills", json={
        "name": "Python", "level": "confident", "evidence": "AnswerAloud"})


def test_detail_404_for_unknown_analysis():
    r = client.get("/api/fit/99999")
    assert r.status_code == 404


def test_detail_roundtrip_and_history_progress():
    _seed_profile()

    r = client.post("/api/fit", json={"jd_text": JD})
    assert r.status_code == 200
    first = r.json()
    assert "FastAPI" in first["missing"]

    # detail endpoint returns the stored run in full
    d = client.get(f"/api/fit/{first['analysis_id']}")
    assert d.status_code == 200
    detail = d.json()
    assert detail["jd_text"] == JD
    assert {m["skill"] for m in detail["matched"]} >= {"Python"}
    assert "FastAPI" in detail["missing"]
    assert detail["suggestions"]
    assert detail["extracted"]["salary_mentions"]
    # history must not leak full JD text (payload stays light)
    h = client.get("/api/fit/history").json()
    assert h and "jd_text" not in h[0]
    assert h[0]["progress"] is None  # first run of this JD: nothing to compare

    # close one gap, re-run the same JD
    client.post("/api/profile/skills", json={
        "name": "FastAPI", "level": "working", "evidence": "AnswerAloud"})
    r = client.post("/api/fit", json={"jd_text": JD})
    assert r.status_code == 200
    second = r.json()
    assert "FastAPI" not in second["missing"]

    h = client.get("/api/fit/history").json()
    assert h[0]["id"] == second["analysis_id"]  # newest first
    prog = h[0]["progress"]
    assert prog is not None
    assert "FastAPI" in prog["closed"]
    assert prog["new"] == []
    # the older run keeps its own (null) progress
    assert h[1]["id"] == first["analysis_id"]
    assert h[1]["progress"] is None


def test_progress_is_scoped_to_identical_jd():
    # a different JD must not diff against the earlier runs
    r = client.post("/api/fit", json={"jd_text": JD_OTHER})
    assert r.status_code == 200
    h = client.get("/api/fit/history").json()
    assert h[0]["id"] == r.json()["analysis_id"]
    assert h[0]["progress"] is None
