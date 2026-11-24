import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.agent import fit, interview

JD = """
SDE Intern - Elip
We are looking for an intern with Python, FastAPI and SQL (PostgreSQL).
Experience with React, Docker and machine learning is a plus.
2+ years of experience preferred. Bachelor's degree in Computer Science.
Salary: 4-6 LPA. Full-time, Hybrid.
"""


def fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    return conn


def seed_profile(conn):
    db.insert(conn, "skill", {"name": "Python", "level": "confident", "evidence": "AnswerAloud"})
    db.insert(conn, "skill", {"name": "FastAPI", "level": "working", "evidence": "AnswerAloud"})
    db.insert(conn, "skill", {"name": "React", "level": "working", "evidence": "Portfolio"})
    db.insert(conn, "project", {
        "title": "AnswerAloud", "description": "Voice learning app",
        "tech": '["Python", "FastAPI", "SQLite"]', "url": "",
        "highlights": "[]"})


def test_fit_matches_and_gaps():
    conn = fresh_conn()
    seed_profile(conn)
    result = fit.analyze(conn, JD)
    matched = {m["skill"] for m in result["matched"]}
    assert {"Python", "FastAPI", "React"} <= matched
    assert "Docker" in result["missing"]
    assert "Machine Learning" in result["missing"]
    # transparent extraction: salary, experience, education, type, mode
    ex = result["extracted"]
    assert ex["salary_mentions"] and "4-6 LPA" in ex["salary_mentions"][0]
    assert ex["experience_mentions"]
    assert "Internship" in ex["employment_types"] or "Full-time" in ex["employment_types"]
    assert "Hybrid" in ex["work_modes"]
    # suggestions route to practice where a category exists
    cats = {s.get("category") for s in result["suggestions"]}
    assert "cs" in cats or "ml" in cats
    # persisted
    row = db.get(conn, "jd_analysis", result["analysis_id"])
    assert row and row["application_id"] is None
    assert result["agent_run_id"]


def test_fit_empty_profile_reports_everything_missing():
    conn = fresh_conn()
    result = fit.analyze(conn, JD)
    assert not result["matched"]
    assert set(result["missing"]) >= {"Python", "FastAPI", "React", "Docker"}


def test_question_bank_seeds_and_project_questions_generate():
    conn = fresh_conn()
    seed_profile(conn)
    n = interview.seed_questions(conn)
    assert n > 20
    # idempotent
    assert interview.seed_questions(conn) == 0
    project_qs = db.query(conn, "SELECT * FROM question WHERE source='generated'")
    assert project_qs and all("AnswerAloud" in q["question"] for q in project_qs)
    # regenerating replaces, not duplicates
    interview.regenerate_project_questions(conn)
    again = db.query(conn, "SELECT * FROM question WHERE source='generated'")
    assert len(again) == len(project_qs)


def test_pick_and_record_session():
    conn = fresh_conn()
    interview.seed_questions(conn)
    q = interview.pick_question(conn, category="sql")
    assert q and q["category"] == "sql" and q["model_points"]
    out = interview.record_session(conn, q["id"], "An index is a B-tree...", 4)
    assert out["session_id"] and out["question"]["id"] == q["id"]
    # recently practiced question is avoided on the next pick
    q2 = interview.pick_question(conn, category="sql")
    assert q2["id"] != q["id"]
    s = interview.stats(conn)
    assert s["total_sessions"] == 1
    assert s["by_category"][0]["category"] == "sql"


def test_record_session_validates_rating():
    conn = fresh_conn()
    interview.seed_questions(conn)
    q = interview.pick_question(conn)
    try:
        interview.record_session(conn, q["id"], "answer", 9)
        assert False, "should reject rating > 5"
    except ValueError:
        pass
