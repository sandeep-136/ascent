"""Ascent - personal AI career agent. FastAPI application."""

import json
import os

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db
from .agent import fit, interview, planner

app = FastAPI(title="Ascent", version="0.1.0",
              description="A personal AI career agent: daily plan, application tracking, "
                          "JD fit analysis and mock interviews.")


@app.middleware("http")
async def access_gate(request: Request, call_next):
    """Single-owner access control.

    Private instance: every /api route except /api/auth/* requires a valid
    owner session cookie. Public demo (ASCENT_DEMO=1): reads are open, all
    mutations and all LLM endpoints are rejected so the fictional demo can
    never store real personal data or spend a provider key.
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path.startswith("/api/auth/"):
        return await call_next(request)
    if auth.demo_mode():
        # The only mutation a visitor may trigger: regenerating the plan from
        # the seeded fictional data. It stores nothing the visitor supplies.
        plan_run = path == "/api/plan/run" and request.method == "POST"
        if not plan_run and (path.startswith("/api/llm/") or request.method not in ("GET", "HEAD")):
            return JSONResponse({"detail": {"error": "demo_read_only"}}, status_code=403)
        return await call_next(request)
    if path == "/api/status":
        return await call_next(request)
    # CSRF: mutations require a non-simple header a cross-site form cannot set.
    if request.method not in ("GET", "HEAD") and not path.startswith("/api/auth/"):
        if request.headers.get(auth.CSRF_HEADER) != "1":
            return JSONResponse({"detail": {"error": "csrf_header_missing"}}, status_code=403)
    c = conn()
    try:
        token = request.cookies.get(auth.COOKIE_NAME)
        if not auth.valid_token(c, token):
            return JSONResponse({"detail": {"error": "auth_required"}}, status_code=401)
    finally:
        c.close()
    return await call_next(request)


# ---------- auth ----------

class PassphraseIn(BaseModel):
    passphrase: str


def _set_session(response: Response, c) -> None:
    secure = os.environ.get("ASCENT_SECURE_COOKIE") == "1"
    response.set_cookie(auth.COOKIE_NAME, auth.issue_token(c),
                        max_age=auth.SESSION_TTL_SECONDS, httponly=True,
                        samesite="lax", secure=secure)


@app.get("/api/auth/me")
def auth_me(request: Request):
    c = conn()
    try:
        import os
        needs = auth.needs_setup(c)
        return {"authenticated": auth.valid_token(c, request.cookies.get(auth.COOKIE_NAME)),
                "needs_setup": needs, "demo": auth.demo_mode(),
                "setup_code_required": needs and bool(os.environ.get("ASCENT_BOOTSTRAP_TOKEN"))}
    finally:
        c.close()


@app.post("/api/auth/setup")
def auth_setup(body: PassphraseIn, request: Request, response: Response):
    if auth.demo_mode():
        raise HTTPException(403, "demo instance has no owner setup")
    host = request.client.host if request.client else None
    if not auth.setup_allowed(host, request.headers.get(auth.BOOTSTRAP_HEADER)):
        raise HTTPException(403, "setup is only allowed from localhost or with the bootstrap token")
    c = conn()
    try:
        try:
            auth.setup_passphrase(c, body.passphrase)
        except ValueError as exc:
            raise HTTPException(409 if "already" in str(exc) else 422, str(exc))
        _set_session(response, c)
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/auth/login")
def auth_login(body: PassphraseIn, response: Response):
    c = conn()
    try:
        if not auth.verify_passphrase(c, body.passphrase):
            raise HTTPException(401, "wrong passphrase")
        _set_session(response, c)
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    if request.headers.get(auth.CSRF_HEADER) != "1":
        raise HTTPException(403, {"error": "csrf_header_missing"})
    c = conn()
    try:
        auth.bump_epoch(c)  # invalidates every outstanding session token
    finally:
        c.close()
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def conn() -> "object":
    c = db.connect()
    db.init_db(c)
    return c


@app.on_event("startup")
def startup():
    c = conn()
    interview.seed_questions(c)
    c.close()
    if auth.demo_mode():
        # Public demo: seed the fictional persona so visitors see a full app.
        import subprocess
        import sys
        subprocess.run([sys.executable, "samples/seed_demo.py"], check=False)


# ---------- schemas ----------

class ProfileFields(BaseModel):
    fields: dict[str, str]

class SkillIn(BaseModel):
    name: str
    level: str = Field(default="learning", pattern="^(learning|working|confident)$")
    evidence: str = ""

class ProjectIn(BaseModel):
    title: str
    description: str = ""
    tech: list[str] = []
    url: str = ""
    highlights: list[str] = []

class ApplicationIn(BaseModel):
    company: str
    role: str
    url: str = ""
    source: str = ""
    status: str = Field(default="saved",
                        pattern="^(saved|applied|oa|interview|offer|rejected|withdrawn)$")
    applied_on: str | None = None
    deadline: str | None = None
    follow_up_due: str | None = None
    jd_text: str = ""
    notes: str = ""

class InterviewEventIn(BaseModel):
    application_id: int | None = None
    company: str = ""
    role: str = ""
    kind: str = Field(default="technical", pattern="^(phone|oa|technical|hr|other)$")
    scheduled_at: str
    status: str = Field(default="upcoming", pattern="^(upcoming|done|cancelled)$")
    outcome: str = ""
    notes: str = ""

class TaskIn(BaseModel):
    title: str
    kind: str = "custom"
    due_date: str | None = None

class FitIn(BaseModel):
    jd_text: str
    application_id: int | None = None

class AnswerIn(BaseModel):
    question_id: int
    answer_text: str
    self_rating: int | None = Field(default=None, ge=1, le=5)
    mode: str = Field(default="practice", pattern="^(practice|mock)$")


# ---------- pages ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------- profile ----------

@app.get("/api/profile")
def get_profile():
    c = conn()
    try:
        profile = fit.load_profile(c)
        stats = interview.stats(c)
        profile["practice"] = stats
        return profile
    finally:
        c.close()


@app.put("/api/profile/fields")
def put_profile_fields(body: ProfileFields):
    c = conn()
    try:
        for key, value in body.fields.items():
            c.execute("INSERT INTO profile_field (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, value))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/profile/skills", status_code=201)
def add_skill(body: SkillIn):
    c = conn()
    try:
        c.execute("INSERT INTO skill (name, level, evidence) VALUES (?, ?, ?) "
                  "ON CONFLICT(name) DO UPDATE SET level = excluded.level, "
                  "evidence = excluded.evidence",
                  (body.name, body.level, body.evidence))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@app.delete("/api/profile/skills/{name}")
def delete_skill(name: str):
    c = conn()
    try:
        c.execute("DELETE FROM skill WHERE name = ?", (name,))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/profile/projects", status_code=201)
def add_project(body: ProjectIn):
    c = conn()
    try:
        pid = db.insert(c, "project", {
            "title": body.title, "description": body.description,
            "tech": json.dumps(body.tech, ensure_ascii=False),
            "url": body.url,
            "highlights": json.dumps(body.highlights, ensure_ascii=False)})
        interview.regenerate_project_questions(c)
        return {"id": pid}
    finally:
        c.close()


@app.put("/api/profile/projects/{pid}")
def put_project(pid: int, body: ProjectIn):
    c = conn()
    try:
        if not db.get(c, "project", pid):
            raise HTTPException(404, "project not found")
        db.update(c, "project", pid, {
            "title": body.title, "description": body.description,
            "tech": json.dumps(body.tech, ensure_ascii=False),
            "url": body.url,
            "highlights": json.dumps(body.highlights, ensure_ascii=False)})
        interview.regenerate_project_questions(c)
        return {"ok": True}
    finally:
        c.close()


@app.delete("/api/profile/projects/{pid}")
def delete_project(pid: int):
    c = conn()
    try:
        db.delete(c, "project", pid)
        interview.regenerate_project_questions(c)
        return {"ok": True}
    finally:
        c.close()


# ---------- applications ----------

@app.get("/api/applications")
def list_applications():
    c = conn()
    try:
        return db.query(c, "SELECT * FROM application ORDER BY updated_at DESC")
    finally:
        c.close()


@app.post("/api/applications", status_code=201)
def add_application(body: ApplicationIn):
    c = conn()
    try:
        now = db.now()
        aid = db.insert(c, "application", {**body.model_dump(),
                                           "created_at": now, "updated_at": now})
        return {"id": aid}
    finally:
        c.close()


@app.put("/api/applications/{aid}")
def put_application(aid: int, body: ApplicationIn):
    c = conn()
    try:
        if not db.get(c, "application", aid):
            raise HTTPException(404, "application not found")
        db.update(c, "application", aid, {**body.model_dump(), "updated_at": db.now()})
        return {"ok": True}
    finally:
        c.close()


@app.delete("/api/applications/{aid}")
def delete_application(aid: int):
    c = conn()
    try:
        db.delete(c, "application", aid)
        return {"ok": True}
    finally:
        c.close()


# ---------- interview events ----------

@app.get("/api/interviews")
def list_interviews():
    c = conn()
    try:
        return db.query(c, "SELECT * FROM interview_event ORDER BY scheduled_at")
    finally:
        c.close()


@app.post("/api/interviews", status_code=201)
def add_interview(body: InterviewEventIn):
    c = conn()
    try:
        return {"id": db.insert(c, "interview_event",
                                {**body.model_dump(), "created_at": db.now()})}
    finally:
        c.close()


@app.put("/api/interviews/{iid}")
def put_interview(iid: int, body: InterviewEventIn):
    c = conn()
    try:
        if not db.get(c, "interview_event", iid):
            raise HTTPException(404, "interview not found")
        db.update(c, "interview_event", iid, body.model_dump())
        return {"ok": True}
    finally:
        c.close()


@app.delete("/api/interviews/{iid}")
def delete_interview(iid: int):
    c = conn()
    try:
        db.delete(c, "interview_event", iid)
        return {"ok": True}
    finally:
        c.close()


# ---------- daily plan & tasks ----------

@app.post("/api/plan/run")
def run_plan():
    c = conn()
    try:
        return planner.run(c)
    finally:
        c.close()


@app.get("/api/tasks")
def list_tasks(status: str = "open"):
    c = conn()
    try:
        return db.query(c, "SELECT * FROM task WHERE status = ? ORDER BY score DESC, due_date",
                        (status,))
    finally:
        c.close()


@app.post("/api/tasks", status_code=201)
def add_task(body: TaskIn):
    c = conn()
    try:
        return {"id": db.insert(c, "task", {
            "title": body.title, "kind": body.kind, "due_date": body.due_date,
            "status": "open", "score": 50, "why": "Added by you.",
            "source": "user", "created_at": db.now()})}
    finally:
        c.close()


@app.post("/api/tasks/{tid}/done")
def done_task(tid: int):
    c = conn()
    try:
        if not db.get(c, "task", tid):
            raise HTTPException(404, "task not found")
        db.update(c, "task", tid, {"status": "done", "completed_at": db.now()})
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/tasks/{tid}/skip")
def skip_task(tid: int):
    c = conn()
    try:
        if not db.get(c, "task", tid):
            raise HTTPException(404, "task not found")
        db.update(c, "task", tid, {"status": "skipped"})
        return {"ok": True}
    finally:
        c.close()


# ---------- JD fit ----------

@app.post("/api/fit")
def analyze_fit(body: FitIn):
    c = conn()
    try:
        if len(body.jd_text.strip()) < 40:
            raise HTTPException(400, "paste the full job description text")
        return fit.analyze(c, body.jd_text, body.application_id)
    finally:
        c.close()


@app.get("/api/fit/history")
def fit_history():
    c = conn()
    try:
        rows = db.query(c, "SELECT id, application_id, jd_text, matched, missing, similarity, "
                           "created_at FROM jd_analysis "
                           "ORDER BY created_at DESC, id DESC LIMIT 50")
        for r in rows:
            r["matched"] = json.loads(r["matched"] or "[]")
            r["missing"] = json.loads(r["missing"] or "[]")
        # Gap progress: for each run, diff its gaps against the previous run
        # of the same JD text (re-analysis after a profile update shows what
        # closed and what newly appeared).
        prev_missing: dict[str, set] = {}
        for r in reversed(rows):  # oldest first
            cur = set(r["missing"])
            prev = prev_missing.get(r["jd_text"])
            r["progress"] = ({"closed": sorted(prev - cur), "new": sorted(cur - prev)}
                             if prev is not None else None)
            prev_missing[r["jd_text"]] = cur
            del r["jd_text"]
        return rows
    finally:
        c.close()


@app.get("/api/fit/{analysis_id}")
def fit_detail(analysis_id: int):
    c = conn()
    try:
        row = db.get(c, "jd_analysis", analysis_id)
        if not row:
            raise HTTPException(404, "analysis not found")
        row["extracted"] = json.loads(row["extracted"] or "{}")
        for k in ("matched", "missing", "suggestions"):
            row[k] = json.loads(row[k] or "[]")
        return row
    finally:
        c.close()


# ---------- interview practice ----------

@app.get("/api/interview/question")
def get_question(category: str | None = None, tag: str | None = None):
    c = conn()
    try:
        q = interview.pick_question(c, category, tag)
        if not q:
            raise HTTPException(404, "no questions available")
        return q
    finally:
        c.close()


@app.post("/api/interview/answer", status_code=201)
def post_answer(body: AnswerIn):
    c = conn()
    try:
        if not db.get(c, "question", body.question_id):
            raise HTTPException(404, "question not found")
        return interview.record_session(c, body.question_id, body.answer_text,
                                        body.self_rating, body.mode)
    finally:
        c.close()


class SessionStartIn(BaseModel):
    category: str | None = None
    length: int = Field(default=5, ge=3, le=8)

class SessionAnswerIn(BaseModel):
    question_id: int
    answer_text: str = Field(min_length=1, max_length=20000)
    self_rating: int | None = Field(default=None, ge=1, le=5)

@app.post("/api/interview/sessions", status_code=201)
def start_mock_session(body: SessionStartIn):
    c = conn()
    try:
        try:
            return interview.start_session(c, body.category or None, body.length)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    finally:
        c.close()

@app.get("/api/interview/sessions/{sid}")
def mock_session_state(sid: int):
    c = conn()
    try:
        try:
            return interview.session_state(c, sid)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
    finally:
        c.close()

@app.post("/api/interview/sessions/{sid}/answer")
def answer_mock_session(sid: int, body: SessionAnswerIn):
    c = conn()
    try:
        try:
            return interview.answer_in_session(c, sid, body.question_id,
                                               body.answer_text, body.self_rating)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    finally:
        c.close()

@app.get("/api/interview/history")
def interview_history():
    c = conn()
    try:
        return interview.history(c)
    finally:
        c.close()

@app.get("/api/interview/stats")
def interview_stats():
    c = conn()
    try:
        return interview.stats(c)
    finally:
        c.close()


# ---------- llm (preview-before-send) ----------

class LlmPreviewIn(BaseModel):
    feature: str = Field(pattern="^(gap_brief|answer_feedback)$")
    analysis_id: int | None = None
    question_id: int | None = None
    answer: str | None = None

class LlmSendIn(BaseModel):
    payload_id: str

@app.post("/api/llm/preview", status_code=201)
def llm_preview(body: LlmPreviewIn):
    """Build the exact outbound payload for an LLM feature and return it for
    review. Nothing is sent to any provider here."""
    from .llm import features
    c = conn()
    try:
        params = {k: v for k, v in body.model_dump().items() if v is not None and k != "feature"}
        try:
            return features.preview(c, body.feature, params)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    finally:
        c.close()

@app.post("/api/llm/send")
def llm_send(body: LlmSendIn, request: Request):
    """Send a previously previewed payload. The payload is re-read from the
    store (never taken from the client), the privacy guard is re-run in
    enforce mode, then the provider call is made."""
    from .llm import client as llm_client
    from .llm import features
    from .llm import policy as llm_policy
    c = conn()
    try:
        try:
            ip = request.client.host if request.client else "unknown"
            return features.send(c, body.payload_id, ip=ip)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except llm_policy.LLMPrivacyBlock as exc:
            raise HTTPException(403, {"error": "privacy_block", "findings": exc.findings})
        except llm_client.LLMUnavailable as exc:
            raise HTTPException(503, str(exc))
        except llm_client.LLMRateLimited as exc:
            raise HTTPException(429, str(exc))
    finally:
        c.close()


# ---------- agent transparency ----------

@app.get("/api/agent/runs")
def agent_runs():
    c = conn()
    try:
        rows = db.query(c, "SELECT * FROM agent_run ORDER BY created_at DESC LIMIT 20")
        for r in rows:
            r["input"] = json.loads(r["input"] or "{}")
            r["steps"] = json.loads(r["steps"] or "[]")
            r["output"] = json.loads(r["output"] or "{}")
        return rows
    finally:
        c.close()


@app.get("/api/status")
def status(request: Request):
    from .llm import client as llm_client
    from .llm import policy as llm_policy
    pol = llm_policy.load_policy()
    return {
        "llm_configured": llm_client.server_llm_available(),
        "privacy_mode": pol.mode,
        "version": app.version,
        # Render injects RENDER_GIT_COMMIT at build/run time; empty locally.
        # Lets deploy verification confirm which commit the live process runs.
        "commit": os.environ.get("RENDER_GIT_COMMIT", ""),
    }
