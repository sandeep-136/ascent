import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.agent import planner

TODAY = dt.date(2026, 11, 24)
ISO = lambda d: d.isoformat()


def fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    return conn


def test_interview_tomorrow_tops_plan():
    conn = fresh_conn()
    db.insert(conn, "interview_event", {
        "company": "Acme", "role": "SDE Intern", "kind": "technical",
        "scheduled_at": ISO(TODAY + dt.timedelta(days=1)) + "T10:00:00",
        "status": "upcoming", "created_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    assert plan["items"][0]["kind"] == "interview_prep"
    assert "Acme" in plan["items"][0]["title"]
    assert "tomorrow" in plan["items"][0]["why"]


def test_deadline_within_window():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "Elip", "role": "SDE Intern", "status": "saved",
        "deadline": ISO(TODAY + dt.timedelta(days=2)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    kinds = [i["kind"] for i in plan["items"]]
    assert "application" in kinds


def test_far_deadline_not_planned():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "FarAway", "role": "SDE", "status": "saved",
        "deadline": ISO(TODAY + dt.timedelta(days=30)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    assert all(i["kind"] != "application" for i in plan["items"])


def test_followup_due_and_overdue():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "Zebra", "role": "QA", "status": "applied",
        "applied_on": ISO(TODAY - dt.timedelta(days=12)),
        "follow_up_due": ISO(TODAY - dt.timedelta(days=2)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    fu = [i for i in plan["items"] if i["kind"] == "followup"]
    assert fu and "overdue" not in fu[0]["why"] or "ago" in fu[0]["why"]
    assert "Zebra" in fu[0]["title"]


def test_old_application_suggests_followup():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "OldCo", "role": "SDE", "status": "applied",
        "applied_on": ISO(TODAY - dt.timedelta(days=9)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    fu = [i for i in plan["items"] if i["kind"] == "followup" and "OldCo" in i["title"]]
    assert fu and "9 days ago" in fu[0]["why"]


def test_quiet_day_fallback_adds_prep():
    conn = fresh_conn()
    plan = planner.build_plan(conn, TODAY)
    assert plan["items"]  # never an empty day
    assert any(i["kind"] == "study" for i in plan["items"])


def test_sync_tasks_is_idempotent_and_skips_stale():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "Elip", "role": "SDE Intern", "status": "saved",
        "deadline": ISO(TODAY + dt.timedelta(days=1)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.run(conn, TODAY)
    open1 = db.query(conn, "SELECT * FROM task WHERE status='open' AND source='planner'")
    planner.run(conn, TODAY)  # re-run: no duplicates
    open2 = db.query(conn, "SELECT * FROM task WHERE status='open' AND source='planner'")
    assert len(open1) == len(open2)
    # trigger disappears -> task skipped
    db.update(conn, "application", open1 and plan["items"][0]["ref_id"] or 1, {"status": "applied"})
    planner.run(conn, TODAY + dt.timedelta(days=1))
    skipped = db.query(conn, "SELECT * FROM task WHERE status='skipped'")
    assert skipped
    assert plan["agent_run_id"]
    trace = db.get(conn, "agent_run", plan["agent_run_id"])
    assert trace and trace["kind"] == "plan"


def test_every_item_has_a_reason():
    conn = fresh_conn()
    db.insert(conn, "interview_event", {
        "company": "Acme", "role": "SDE", "kind": "hr",
        "scheduled_at": ISO(TODAY) + "T15:00:00", "status": "upcoming",
        "created_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    assert plan["steps"]
    for item in plan["items"]:
        assert item["why"], item
        assert item["score"] > 0


def test_overdue_deadline_escalates():
    conn = fresh_conn()
    db.insert(conn, "application", {
        "company": "Elip", "role": "SDE Intern", "status": "saved",
        "deadline": ISO(TODAY - dt.timedelta(days=2)),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    item = next(i for i in plan["items"] if i["kind"] == "application")
    assert "Deadline passed" in item["title"]
    assert "2 day(s) ago" in item["why"]


def test_past_interview_prompts_outcome():
    conn = fresh_conn()
    db.insert(conn, "interview_event", {
        "company": "Acme", "role": "SDE", "kind": "technical",
        "scheduled_at": ISO(TODAY - dt.timedelta(days=1)) + "T10:00:00",
        "status": "upcoming", "created_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    assert any(i["kind"] == "interview_outcome" and "Acme" in i["title"]
               for i in plan["items"])


def test_done_interview_not_prompted():
    conn = fresh_conn()
    db.insert(conn, "interview_event", {
        "company": "Acme", "role": "SDE", "kind": "technical",
        "scheduled_at": ISO(TODAY - dt.timedelta(days=1)) + "T10:00:00",
        "status": "done", "created_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    assert not any(i["kind"] in ("interview_outcome", "interview_prep")
                   for i in plan["items"])


def test_quiet_day_practice_targets_stale_category():
    conn = fresh_conn()
    q_sql = db.insert(conn, "question", {
        "category": "sql", "difficulty": "easy", "tags": "[]",
        "question": "q sql", "model_points": "[]", "source": "bank"})
    q_dsa = db.insert(conn, "question", {
        "category": "dsa", "difficulty": "easy", "tags": "[]",
        "question": "q dsa", "model_points": "[]", "source": "bank"})
    base = dt.datetime.combine(TODAY, dt.time(12, 0)).timestamp()
    db.insert(conn, "practice_session", {
        "question_id": q_sql, "self_rating": 5, "created_at": base})
    db.insert(conn, "practice_session", {
        "question_id": q_dsa, "self_rating": 2, "created_at": base - 5 * 86400})
    plan = planner.build_plan(conn, TODAY)
    item = next(i for i in plan["items"]
                if i["kind"] == "study" and i["title"].startswith("Practice"))
    assert "DSA" in item["title"]
    assert "5 day(s)" in item["why"] and "2.0/5" in item["why"]


def test_quiet_day_prep_uses_frequent_gap():
    conn = fresh_conn()
    for missing in ('["Docker", "SQL"]', '["Docker", "React"]'):
        db.insert(conn, "jd_analysis", {
            "jd_text": "x" * 50, "missing": missing, "created_at": db.now()})
    plan = planner.build_plan(conn, TODAY)
    item = next(i for i in plan["items"] if i["ref_kind"] == "jd_analysis")
    assert "Docker" in item["title"]
    assert "2 of your last 2 analysed role(s)" in item["title"]
