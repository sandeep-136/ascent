"""LLM preview-before-send flow tests. No provider is called."""

import json
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


def _mk_analysis():
    r = client.post("/api/fit", json={"jd_text": "Need Python, FastAPI, Kubernetes and Redis."})
    assert r.status_code == 200
    return r.json()["analysis_id"]


def test_gap_brief_preview_builds_minimal_payload():
    aid = _mk_analysis()
    r = client.post("/api/llm/preview", json={"feature": "gap_brief", "analysis_id": aid})
    assert r.status_code == 201
    body = r.json()
    assert body["tier"] == "structured"
    assert "Kubernetes" in body["user"]
    assert body["payload_id"]
    # structured tier: no findings expected from a skill list
    assert body["findings"] == []


def test_answer_feedback_preview_flags_email():
    q = client.get("/api/interview/question", params={"category": "python"}).json()
    r = client.post("/api/llm/preview", json={
        "feature": "answer_feedback", "question_id": q["id"],
        "answer": "I used generators, contact me at test@example.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["tier"] == "free_text"
    kinds = {f["kind"] for f in body["findings"]}
    assert "email" in kinds
    assert body["blocked_in_current_mode"] is True


def test_send_unknown_payload_404():
    r = client.post("/api/llm/send", json={"payload_id": "nope"})
    assert r.status_code == 404


def test_send_blocked_by_privacy_guard():
    q = client.get("/api/interview/question", params={"category": "sql"}).json()
    p = client.post("/api/llm/preview", json={
        "feature": "answer_feedback", "question_id": q["id"],
        "answer": "email me at person@example.com"}).json()
    r = client.post("/api/llm/send", json={"payload_id": p["payload_id"]})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "privacy_block"


def test_send_without_provider_is_503_not_crash():
    aid = _mk_analysis()
    p = client.post("/api/llm/preview", json={"feature": "gap_brief", "analysis_id": aid}).json()
    assert p["findings"] == []
    r = client.post("/api/llm/send", json={"payload_id": p["payload_id"]})
    # no provider key configured in tests: a clean 503, not a 500
    assert r.status_code == 503
