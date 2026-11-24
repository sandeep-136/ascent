"""Auth + demo-mode isolation tests: unauthorized reads/writes must be denied."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fd, DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _env():
    os.environ["ASCENT_DB"] = DB_PATH
    os.environ.pop("ASCENT_DEMO", None)
    yield
    os.environ.pop("ASCENT_DEMO", None)


PROTECTED_GETS = ["/api/tasks", "/api/profile", "/api/applications",
                  "/api/interviews", "/api/fit/history", "/api/interview/stats",
                  "/api/agent/runs"]
PROTECTED_MUTATIONS = [
    ("post", "/api/plan/run", {}),
    ("post", "/api/tasks", {"title": "x"}),
    ("post", "/api/fit", {"jd_text": "Need Python and FastAPI for this role."}),
    ("post", "/api/llm/preview", {"feature": "gap_brief", "analysis_id": 1}),
    ("post", "/api/llm/send", {"payload_id": "x"}),
    ("put", "/api/profile/fields", {"fields": {"name": "x"}}),
]


def test_needs_setup_and_all_routes_denied_without_session():
    with TestClient(app, headers={"X-Ascent-Client": "1"}) as anon:
        assert anon.get("/api/auth/me").json()["needs_setup"] is True
        for path in PROTECTED_GETS:
            assert anon.get(path).status_code == 401, path
        for method, path, body in PROTECTED_MUTATIONS:
            assert getattr(anon, method)(path, json=body).status_code == 401, path


def test_setup_login_and_authorized_access():
    with TestClient(app, headers={"X-Ascent-Client": "1"}) as c:
        r = c.post("/api/auth/setup", json={"passphrase": "correct horse battery"})
        assert r.status_code == 200
        assert c.get("/api/auth/me").json()["authenticated"] is True
        for path in PROTECTED_GETS:
            assert c.get(path).status_code == 200, path
        # second setup is refused
        assert c.post("/api/auth/setup", json={"passphrase": "another one 123"}).status_code == 409
        # wrong passphrase is refused; right one works
        assert c.post("/api/auth/login", json={"passphrase": "nope nope nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"passphrase": "correct horse battery"}).status_code == 200


def test_forged_session_cookie_denied():
    with TestClient(app, headers={"X-Ascent-Client": "1"}) as c:
        c.cookies.set("ascent_session", "9999999999.deadbeef")
        assert c.get("/api/tasks").status_code == 401


def test_demo_mode_blocks_mutations_and_llm_but_allows_reads():
    os.environ["ASCENT_DEMO"] = "1"
    with TestClient(app, headers={"X-Ascent-Client": "1"}) as c:
        for path in PROTECTED_GETS:
            assert c.get(path).status_code == 200, path
        for method, path, body in PROTECTED_MUTATIONS:
            r = getattr(c, method)(path, json=body)
            assert r.status_code == 403 and r.json()["detail"]["error"] == "demo_read_only", path
        assert c.post("/api/auth/setup", json={"passphrase": "whatever12"}).status_code == 403


def test_csrf_header_required_on_mutations():
    from fastapi.testclient import TestClient as BareClient
    with BareClient(app) as bare:  # no X-Ascent-Client header
        assert bare.get("/api/tasks").status_code == 401  # auth, not csrf
        r = bare.post("/api/tasks", json={"title": "x"})
        assert r.status_code == 403 and r.json()["detail"]["error"] == "csrf_header_missing"
        r = bare.post("/api/fit", json={"jd_text": "Need Python and FastAPI for this role."})
        assert r.status_code == 403


def test_logout_invalidates_existing_session():
    with TestClient(app, headers={"X-Ascent-Client": "1"}) as c:
        c.post("/api/auth/login", json={"passphrase": "correct horse battery"})
        assert c.get("/api/tasks").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/tasks").status_code == 401  # old token is dead, not just deleted


def test_setup_gate_localhost_or_bootstrap():
    from app import auth
    assert auth.setup_allowed("127.0.0.1", None) is True
    assert auth.setup_allowed("203.0.113.9", None) is False
    os.environ["ASCENT_BOOTSTRAP_TOKEN"] = "one-time-secret"
    assert auth.setup_allowed("203.0.113.9", "wrong") is False
    assert auth.setup_allowed("203.0.113.9", "one-time-secret") is True
    os.environ.pop("ASCENT_BOOTSTRAP_TOKEN")
    assert auth.setup_allowed("203.0.113.9", "one-time-secret") is False


def test_concurrent_setup_single_winner():
    import threading
    from app import auth as auth_mod
    from app import db as dbmod
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    results = {"ok": 0, "refused": 0}

    def claim():
        c = dbmod.connect(path)
        dbmod.init_db(c)
        try:
            auth_mod.setup_passphrase(c, "racer passphrase")
            results["ok"] += 1
        except ValueError:
            results["refused"] += 1
        finally:
            c.close()

    threads = [threading.Thread(target=claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results["ok"] == 1 and results["refused"] == 3


def test_demo_mode_disables_llm_even_with_key_configured():
    os.environ["ASCENT_DEMO"] = "1"
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["LLM_API_KEY"] = "fake-key-for-test"
    try:
        from app.llm import client as llm_client
        with pytest.raises(llm_client.LLMUnavailable):
            llm_client.complete("sys", "user")
    finally:
        os.environ.pop("ASCENT_DEMO", None)
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("LLM_API_KEY", None)
