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


def test_full_flow():
    # profile
    r = client.put("/api/profile/fields", json={"fields": {
        "name": "Test Candidate", "headline": "CS student",
        "summary": "Python developer building AI tools"}})
    assert r.status_code == 200
    assert client.post("/api/profile/skills", json={
        "name": "Python", "level": "confident", "evidence": "AnswerAloud"}).status_code == 201
    r = client.post("/api/profile/projects", json={
        "title": "AnswerAloud", "description": "Voice learning app",
        "tech": ["Python", "FastAPI"], "url": ""})
    assert r.status_code == 201
    # generated project questions exist
    r = client.get("/api/interview/question", params={"category": "project"})
    assert r.status_code == 200 and "AnswerAloud" in r.json()["question"]
    # application
    r = client.post("/api/applications", json={
        "company": "Elip", "role": "SDE Intern", "status": "saved",
        "deadline": "2099-01-01", "jd_text": JD})
    assert r.status_code == 201
    aid = r.json()["id"]
    # plan
    r = client.post("/api/plan/run")
    assert r.status_code == 200
    plan = r.json()
    assert plan["items"] and plan["steps"] and plan["agent_run_id"]
    # fit
    r = client.post("/api/fit", json={"jd_text": JD, "application_id": aid})
    assert r.status_code == 200
    body = r.json()
    assert any(m["skill"] == "Python" for m in body["matched"])
    assert "Docker" in body["missing"]
    # interview answer
    r = client.get("/api/interview/question", params={"category": "sql"})
    q = r.json()
    r = client.post("/api/interview/answer", json={
        "question_id": q["id"], "answer_text": "An index is a B-tree...", "self_rating": 4})
    assert r.status_code == 201 and r.json()["question"]["model_points"]
    # agent trace recorded
    runs = client.get("/api/agent/runs").json()
    assert any(run["kind"] == "plan" for run in runs)
    # status
    s = client.get("/api/status").json()
    assert s["llm_configured"] is False and s["privacy_mode"] == "block"
    # index page serves
    assert client.get("/").status_code == 200


def test_validation():
    assert client.post("/api/fit", json={"jd_text": "short"}).status_code == 400
    assert client.post("/api/interview/answer", json={
        "question_id": 99999, "answer_text": "x"}).status_code == 404
    assert client.post("/api/tasks/99999/done").status_code == 404


def test_question_tag_param():
    # tag the bank lacks falls back to the category pool (never a dead end)
    r = client.get("/api/interview/question", params={"category": "sql", "tag": "PostgreSQL"})
    assert r.status_code == 200
    assert r.json()["category"] == "sql"


def test_status_reports_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def")
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["commit"] == "abc123def"


def test_interview_round_crud_and_outcome():
    r = client.post("/api/interviews", json={
        "company": "Acme", "role": "SDE Intern", "kind": "technical",
        "scheduled_at": "2099-01-10T10:00:00", "status": "upcoming"})
    assert r.status_code == 201
    iid = r.json()["id"]
    # log the outcome
    r = client.put(f"/api/interviews/{iid}", json={
        "company": "Acme", "role": "SDE Intern", "kind": "technical",
        "scheduled_at": "2099-01-10T10:00:00", "status": "done",
        "outcome": "Moved to next round"})
    assert r.status_code == 200
    rows = client.get("/api/interviews").json()
    row = next(x for x in rows if x["id"] == iid)
    assert row["status"] == "done" and row["outcome"] == "Moved to next round"
    # unknown id
    assert client.put("/api/interviews/99999", json={
        "company": "X", "role": "", "kind": "hr",
        "scheduled_at": "2099-01-01T09:00:00", "status": "done"}).status_code == 404
    # invalid kind rejected
    assert client.post("/api/interviews", json={
        "company": "X", "role": "", "kind": "bogus",
        "scheduled_at": "2099-01-01T09:00:00", "status": "upcoming"}).status_code == 422
    assert client.delete(f"/api/interviews/{iid}").status_code == 200
    assert not any(x["id"] == iid for x in client.get("/api/interviews").json())
