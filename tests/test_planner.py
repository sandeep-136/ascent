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
