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
     Interview passed, still 'upcoming' -> log the outcome
  2. Application/OA deadline within 3d  -> finish the application
     Deadline passed, never applied     -> escalate: apply late or drop
  3. Follow-up due or overdue           -> follow up (draft only)
  4. Applied 7+ days ago, no follow-up  -> suggest a follow-up
  5. Open user-created tasks by due date
  6. Quiet day fallback: prep the skill missing most often across recent
     JD analyses, and practice the interview category whose sessions are
     stalest (or whose self-ratings are weakest on ties)
"""

import datetime as dt
import json
import sqlite3
from collections import Counter

from .. import db

MAX_PLAN_ITEMS = 6
FOLLOW_UP_AFTER_DAYS = 7
INTERVIEW_PREP_WINDOW_DAYS = 2
DEADLINE_WINDOW_DAYS = 3
RECENT_ANALYSES = 5

CATEGORY_LABELS = {"python": "Python", "sql": "SQL", "dsa": "DSA", "web": "Web",
                   "ml": "ML", "cs": "CS", "behavioral": "Behavioral",
                   "project": "Project"}


def _parse_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _days_until(day, today):
    return (day - today).days if day else None


def _practice_target(conn: sqlite3.Connection, today: dt.date) -> dict | None:
    """Pick the practice category needing attention most, with the reason.

    Stalest category wins (days since last session); ties break toward the
    weakest recent self-ratings. Returns None when nothing is stale (e.g.
    everything practiced today, or no sessions recorded yet).
    """
    rows = db.query(conn, """
        SELECT q.category AS category, MAX(s.created_at) AS last_at,
               AVG(s.self_rating) AS avg_rating
        FROM practice_session s JOIN question q ON q.id = s.question_id
        GROUP BY q.category""")
    best: tuple | None = None
    for r in rows:
        last = dt.datetime.fromtimestamp(r["last_at"]).date()
        stale = (today - last).days
        if stale <= 0:
            continue
        key = (-stale, r["avg_rating"] or 0)
        if best is None or key < best[0]:
            best = (key, r["category"], stale, r["avg_rating"])
    if best is None:
        return None
    _, cat, stale, avg = best
    label = CATEGORY_LABELS.get(cat, cat)
    why = f"no {label} practice in {stale} day(s)"
    if avg:
        why += f", recent self-ratings average {avg:.1f}/5"
    return {"category": cat, "label": label, "why": why + "."}


def build_plan(conn: sqlite3.Connection, today: dt.date | None = None) -> dict:
    """Compute the plan and return it with the full decision trace."""
    today = today or dt.date.today()
    steps: list[dict] = []
    items: list[dict] = []

    applications = db.query(conn, "SELECT * FROM application WHERE status NOT IN ('rejected','withdrawn')")
    interviews = db.query(conn, "SELECT * FROM interview_event WHERE status = 'upcoming'")
    open_tasks = db.query(conn, "SELECT * FROM task WHERE status = 'open'")
    steps.append({
        "step": "read_state",
        "detail": f"Read {len(applications)} open application(s), {len(interviews)} upcoming "
                  f"interview(s), {len(open_tasks)} open task(s).",
    })

    # Rule 1: upcoming interviews (and past ones still awaiting an outcome)
    for iv in interviews:
        day = _parse_date(iv["scheduled_at"])
        d = _days_until(day, today)
        if d is not None and d < 0:
            items.append({
                "title": f"Log the outcome of the {iv['kind']} interview at {iv['company'] or 'the company'}",
                "kind": "interview_outcome",
                "ref_kind": "interview_event", "ref_id": iv["id"],
                "due_date": today.isoformat(),
                "score": 65,
                "why": f"The interview was {-d} day(s) ago and is still marked "
                       "upcoming - record how it went so follow-ups stay accurate.",
            })
            steps.append({"step": "rule_interview_outcome",
                          "detail": f"Interview at {iv['company']} passed {-d}d ago -> log outcome."})
        elif d is not None and 0 <= d <= INTERVIEW_PREP_WINDOW_DAYS:
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

    # Rule 2b: deadline passed and the application was never sent
    for app in applications:
        if app["status"] in ("applied", "offer"):
            continue
        day = _parse_date(app["deadline"])
        d = _days_until(day, today)
        if d is not None and d < 0:
            late = -d
            items.append({
                "title": f"Deadline passed for {app['role']} at {app['company']} - apply late or drop it",
                "kind": "application",
                "ref_kind": "application", "ref_id": app["id"],
                "due_date": today.isoformat(),
                "score": 85 - min(late, 14),
                "why": f"The deadline was {late} day(s) ago ({day.isoformat()}) and the "
                       f"status is still '{app['status']}' - send it late or mark it withdrawn.",
            })
            steps.append({"step": "rule_deadline_overdue",
                          "detail": f"{app['company']} deadline passed {late}d ago -> escalate."})

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

    # Rule 6: quiet-day fallback - targeted prep and adaptive practice
    if len(items) < 3:
        # Prep the skill missing most often across recent analyses, not just
        # the first gap of the latest one: frequency across roles is the
        # honest signal of what the market keeps asking for.
        analyses = db.query(
            conn, "SELECT id, missing FROM jd_analysis "
                  "ORDER BY created_at DESC LIMIT ?", (RECENT_ANALYSES,))
        freq: Counter = Counter()
        for a in analyses:
            freq.update(set(json.loads(a["missing"] or "[]")))
        if freq:
            skill, count = freq.most_common(1)[0]
            items.append({
                "title": f"Prep '{skill}' - asked for in {count} of your last {len(analyses)} analysed role(s)",
                "kind": "study",
                "ref_kind": "jd_analysis", "ref_id": analyses[0]["id"],
                "due_date": today.isoformat(),
                "score": 40,
                "why": f"Quiet day: '{skill}' was missing from your profile in "
                       f"{count} of your last {len(analyses)} analysed role(s).",
            })
            steps.append({"step": "rule_quiet_prep",
                          "detail": f"Plan had room; added study prep for '{skill}' "
                                    f"({count}/{len(analyses)} recent roles)."})
        target = _practice_target(conn, today)
        if target:
            items.append({
                "title": f"Practice {target['label']} interview questions",
                "kind": "study",
                "ref_kind": "", "ref_id": None,
                "due_date": today.isoformat(),
                "score": 30,
                "why": "Quiet day: " + target["why"],
            })
            steps.append({"step": "rule_quiet_practice",
                          "detail": f"Plan had room; practice targets {target['label']}: "
                                    + target["why"]})
        else:
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
