"""Daily plan agent.

Reads the owner's career state (applications, interviews, tasks, latest JD
analysis) and produces an ordered plan for the day. Every item carries the
rule that fired and the reason, so the "why" is always visible - this is what
makes the agent explainable instead of a black box.

Fully deterministic: no LLM is needed to produce the plan. When an LLM is
configured it may narrate the plan, but the ordering and reasons come from
these rules alone.

Rules (score order):
  1. Interview within 2 days            -> prepare, highest priority
  2. Application/OA deadline within 3d  -> finish the application
  3. Follow-up due or overdue           -> follow up (draft only)
  4. Applied 7+ days ago, no follow-up  -> suggest a follow-up
  5. Open user-created tasks by due date
  6. Quiet day fallback: skill-gap prep from the latest JD analysis,
     or one mock-interview question
"""

import datetime as dt
import sqlite3

from .. import db

MAX_PLAN_ITEMS = 6
FOLLOW_UP_AFTER_DAYS = 7
INTERVIEW_PREP_WINDOW_DAYS = 2
DEADLINE_WINDOW_DAYS = 3


def _parse_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _days_until(day, today):
    return (day - today).days if day else None


def build_plan(conn: sqlite3.Connection, today: dt.date | None = None) -> dict:
    """Compute the plan and return it with the full decision trace."""
    today = today or dt.date.today()
    steps: list[dict] = []
    items: list[dict] = []

    applications = db.query(conn, "SELECT * FROM application WHERE status NOT IN ('rejected','withdrawn')")
    interviews = db.query(conn, "SELECT * FROM interview_event WHERE status = 'upcoming'")
    open_tasks = db.query(conn, "SELECT * FROM task WHERE status = 'open'")
    latest_analysis = db.query(
        conn, "SELECT * FROM jd_analysis ORDER BY created_at DESC LIMIT 1")
    steps.append({
        "step": "read_state",
        "detail": f"Read {len(applications)} open application(s), {len(interviews)} upcoming "
                  f"interview(s), {len(open_tasks)} open task(s).",
    })

    # Rule 1: upcoming interviews
    for iv in interviews:
        day = _parse_date(iv["scheduled_at"])
        d = _days_until(day, today)
        if d is not None and 0 <= d <= INTERVIEW_PREP_WINDOW_DAYS:
            when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
            items.append({
                "title": f"Prepare for the {iv['kind']} interview at {iv['company'] or 'the company'}",
                "kind": "interview_prep",
                "ref_kind": "interview_event", "ref_id": iv["id"],
                "due_date": day.isoformat(),
                "score": 100 - d * 10,
                "why": f"{iv['kind'].title()} interview is {when} ({day.isoformat()}).",
            })
            steps.append({"step": "rule_interview",
                          "detail": f"Interview at {iv['company']} is {when} -> top priority."})

    # Rule 2: application / OA deadlines close by
    for app in applications:
        if app["status"] in ("applied", "offer"):
            continue
        day = _parse_date(app["deadline"])
        d = _days_until(day, today)
        if d is not None and 0 <= d <= DEADLINE_WINDOW_DAYS:
            when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
            items.append({
                "title": f"Finish the {app['role']} application at {app['company']}",
                "kind": "application",
                "ref_kind": "application", "ref_id": app["id"],
                "due_date": day.isoformat(),
                "score": 90 - d * 10,
                "why": f"Application deadline is {when} ({day.isoformat()}); status is '{app['status']}'.",
            })
            steps.append({"step": "rule_deadline",
                          "detail": f"{app['company']} deadline is {when} -> schedule application work."})

    # Rule 3: follow-up due or overdue
    for app in applications:
        if app["status"] != "applied":
            continue
        day = _parse_date(app["follow_up_due"])
        d = _days_until(day, today)
        if d is not None and d <= 0:
            overdue = -d
            items.append({
                "title": f"Follow up with {app['company']} on the {app['role']} application",
                "kind": "followup",
                "ref_kind": "application", "ref_id": app["id"],
                "due_date": today.isoformat(),
                "score": 70 + min(overdue, 10),
                "why": ("Follow-up was due "
                        + ("today." if overdue == 0 else f"{overdue} day(s) ago.")),
            })
            steps.append({"step": "rule_followup_due",
                          "detail": f"Follow-up with {app['company']} is due -> plan it."})

    # Rule 4: applied a while ago with no follow-up scheduled
    followup_refs = {i["ref_id"] for i in items if i["kind"] == "followup"}
    for app in applications:
        if app["status"] != "applied" or app["id"] in followup_refs:
            continue
        applied = _parse_date(app["applied_on"])
        if applied is None or app["follow_up_due"]:
            continue
        age = (today - applied).days
        if age >= FOLLOW_UP_AFTER_DAYS:
            items.append({
                "title": f"Follow up with {app['company']} on the {app['role']} application",
                "kind": "followup",
                "ref_kind": "application", "ref_id": app["id"],
                "due_date": today.isoformat(),
                "score": 55,
                "why": f"Applied {age} days ago with no follow-up scheduled.",
            })
            steps.append({"step": "rule_followup_suggest",
                          "detail": f"{app['company']}: applied {age}d ago -> suggest follow-up."})

    # Rule 5: the owner's own open tasks
    for task in open_tasks:
        if task["source"] != "user":
            continue
        day = _parse_date(task["due_date"])
        d = _days_until(day, today)
        if d is None:
            score, why = 45, "Your open task (no due date)."
        elif d <= 0:
            score, why = 60 + min(-d, 10), "Your task is due."
        else:
            score, why = max(50 - d, 10), f"Your task is due in {d} day(s)."
        items.append({
            "title": task["title"],
            "kind": task["kind"],
            "ref_kind": task["ref_kind"], "ref_id": task["ref_id"],
            "due_date": task["due_date"],
            "score": score,
            "why": why,
        })

    # Rule 6: quiet-day fallback - prep from the latest JD analysis, or practice
    if len(items) < 3:
        if latest_analysis:
            import json
            analysis = latest_analysis[0]
            missing = json.loads(analysis["missing"] or "[]")
            if missing:
                skill = missing[0]
                items.append({
                    "title": f"Prep '{skill}' - asked for in a role you analysed",
                    "kind": "study",
                    "ref_kind": "jd_analysis", "ref_id": analysis["id"],
                    "due_date": today.isoformat(),
                    "score": 40,
                    "why": "Quiet day: this skill was missing from your profile for the "
                           "most recent job description you analysed.",
                })
                steps.append({"step": "rule_quiet_prep",
                              "detail": f"Plan had room; added study prep for '{skill}'."})
        items.append({
            "title": "Do one mock-interview question",
            "kind": "study",
            "ref_kind": "", "ref_id": None,
            "due_date": today.isoformat(),
            "score": 30,
            "why": "Quiet day: short daily practice keeps interview answers fresh.",
        })
        steps.append({"step": "rule_quiet_practice",
                      "detail": "Plan had room; added one mock-interview question."})

    items.sort(key=lambda i: i["score"], reverse=True)
    items = items[:MAX_PLAN_ITEMS]
    steps.append({"step": "rank",
                  "detail": f"Ranked {len(items)} item(s) by urgency and kept the top {len(items)}."})
    return {"date": today.isoformat(), "items": items, "steps": steps}


def sync_tasks(conn: sqlite3.Connection, plan: dict) -> None:
    """Persist planner items as tasks.

    Existing open planner tasks for the same target are updated in place, so
    re-running the planner never creates duplicates. Planner tasks whose
    trigger disappeared (deadline passed, status changed) are marked skipped.
    """
    seen = set()
    for item in plan["items"]:
        key = (item["kind"], item["ref_kind"] or "", item["ref_id"] or 0, item["title"])
        seen.add(key)
        existing = db.query(
            conn,
            "SELECT * FROM task WHERE status = 'open' AND source = 'planner' "
            "AND kind = ? AND ref_kind = ? AND IFNULL(ref_id, 0) = ?",
            (item["kind"], item["ref_kind"] or "", item["ref_id"] or 0))
        if existing:
            db.update(conn, "task", existing[0]["id"], {
                "score": item["score"], "why": item["why"], "due_date": item["due_date"],
            })
        else:
            db.insert(conn, "task", {
                "title": item["title"], "kind": item["kind"],
                "ref_kind": item["ref_kind"] or "", "ref_id": item["ref_id"],
                "due_date": item["due_date"], "status": "open",
                "score": item["score"], "why": item["why"],
                "source": "planner", "created_at": db.now(),
            })
    stale = db.query(conn, "SELECT * FROM task WHERE status = 'open' AND source = 'planner'")
    for task in stale:
        key = (task["kind"], task["ref_kind"] or "", task["ref_id"] or 0, task["title"])
        if key not in seen:
            db.update(conn, "task", task["id"], {"status": "skipped"})


def run(conn: sqlite3.Connection, today: dt.date | None = None) -> dict:
    """Full agent pass: plan, sync tasks, record the trace."""
    plan = build_plan(conn, today)
    sync_tasks(conn, plan)
    run_id = db.record_agent_run(
        conn, "plan", {"date": plan["date"]}, plan["steps"],
        {"item_count": len(plan["items"]), "items": [i["title"] for i in plan["items"]]})
    plan["agent_run_id"] = run_id
    return plan
