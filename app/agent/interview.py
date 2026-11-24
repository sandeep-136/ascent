"""Mock interview mode.

Two question sources:
  bank      - the curated question bank (app/agent/data/question_bank.json)
  generated - questions built from the owner's own stored projects, so
              "tell me about your project" practice uses his real work

Offline flow (no LLM needed): pick a question, the owner answers, self-rates
1-5, then compares against the model points. When an LLM key is configured,
answer feedback can be generated instead - that path is gated and never faked.
"""

import json
import os
import random
import sqlite3

from .. import db

BANK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "question_bank.json")

PROJECT_TEMPLATES = [
    ("Explain the architecture of {title}. What are the main components and how do they talk to each other?",
     ["Clear component breakdown (frontend, backend, database, external services)",
      "Data flow described from a user action to the response",
      "Why this structure was chosen over alternatives"]),
    ("Why did you choose {tech} for {title}? What else did you consider?",
     ["The actual requirement the technology solved",
      "At least one alternative considered and why it lost",
      "A trade-off accepted by choosing it"]),
    ("What was the hardest technical problem in {title} and how did you solve it?",
     ["The problem stated precisely, not vaguely",
      "The debugging or design process, step by step",
      "What you would do differently now"]),
    ("How does {title} handle errors or bad input?",
     ["Specific failure modes identified",
      "What the user sees when something fails",
      "How errors are logged or surfaced for debugging"]),
    ("How would you scale {title} if usage grew 100x?",
     ["The first bottleneck identified honestly",
      "A concrete scaling step (caching, queue, index, horizontal split)",
      "Recognition of what would NOT need to change"]),
]


def seed_questions(conn: sqlite3.Connection) -> int:
    """Load the curated bank plus generated project questions. Idempotent."""
    existing = db.query(conn, "SELECT COUNT(*) AS c FROM question")[0]["c"]
    if existing:
        return 0
    count = 0
    with open(BANK_PATH, encoding="utf-8") as fh:
        bank = json.load(fh)
    for q in bank:
        db.insert(conn, "question", {
            "category": q["category"], "difficulty": q["difficulty"],
            "tags": json.dumps(q["tags"], ensure_ascii=False),
            "question": q["question"],
            "model_points": json.dumps(q["model_points"], ensure_ascii=False),
            "source": "bank"})
        count += 1
    count += regenerate_project_questions(conn)
    return count


def regenerate_project_questions(conn: sqlite3.Connection) -> int:
    """(Re)build questions from the owner's projects. Called on profile change."""
    conn.execute("DELETE FROM question WHERE source = 'generated'")
    projects = db.query(conn, "SELECT * FROM project")
    count = 0
    for p in projects:
        tech = json.loads(p["tech"] or "[]")
        for template, points in PROJECT_TEMPLATES:
            text = template.format(title=p["title"], tech=tech[0] if tech else "your stack")
            db.insert(conn, "question", {
                "category": "project", "difficulty": "medium",
                "tags": json.dumps([p["title"]] + tech[:3], ensure_ascii=False),
                "question": text,
                "model_points": json.dumps(points, ensure_ascii=False),
                "source": "generated"})
            count += 1
    conn.commit()
    return count


def pick_question(conn: sqlite3.Connection, category: str | None = None,
                  exclude_recent: int = 10) -> dict | None:
    """Pick a question, avoiding the ones practiced most recently."""
    recent = db.query(
        conn,
        "SELECT question_id FROM practice_session ORDER BY created_at DESC LIMIT ?",
        (exclude_recent,))
    recent_ids = {r["question_id"] for r in recent if r["question_id"] is not None}
    if category:
        rows = db.query(conn, "SELECT * FROM question WHERE category = ?", (category,))
    else:
        rows = db.query(conn, "SELECT * FROM question")
    fresh = [r for r in rows if r["id"] not in recent_ids] or rows
    if not fresh:
        return None
    row = dict(random.choice(fresh))
    row["tags"] = json.loads(row["tags"] or "[]")
    row["model_points"] = json.loads(row["model_points"] or "[]")
    return row


def record_session(conn: sqlite3.Connection, question_id: int, answer_text: str,
                   self_rating: int | None, mode: str = "practice") -> dict:
    """Store a practice attempt and return it with the review material."""
    if self_rating is not None and not 1 <= self_rating <= 5:
        raise ValueError("self_rating must be 1-5")
    session_id = db.insert(conn, "practice_session", {
        "question_id": question_id, "mode": mode, "answer_text": answer_text,
        "self_rating": self_rating, "created_at": db.now()})
    question = db.get(conn, "question", question_id)
    if question:
        question["tags"] = json.loads(question["tags"] or "[]")
        question["model_points"] = json.loads(question["model_points"] or "[]")
    db.record_agent_run(
        conn, "interview_feedback",
        {"question_id": question_id, "mode": mode, "answer_chars": len(answer_text)},
        [{"step": "record",
          "detail": "Recorded the attempt; review is self-rated against model points "
                    "(LLM feedback not configured)."}],
        {"session_id": session_id})
    return {"session_id": session_id, "question": question,
            "self_rating": self_rating, "llm_feedback": None}


def stats(conn: sqlite3.Connection) -> dict:
    """Practice progress for the dashboard."""
    by_cat = db.query(conn, """
        SELECT q.category, COUNT(*) AS attempts, AVG(s.self_rating) AS avg_rating
        FROM practice_session s JOIN question q ON q.id = s.question_id
        GROUP BY q.category ORDER BY attempts DESC""")
    total = db.query(conn, "SELECT COUNT(*) AS c FROM practice_session")[0]["c"]
    return {"total_sessions": total, "by_category": by_cat}

# A good interviewer digs one level deeper. Deterministic follow-ups per
# category so the practice loop keeps going without an LLM.
FOLLOWUPS = {
    "python": "How would you test that? Name the cases you would write first.",
    "sql": "How does that behave on 10 million rows? Which index changes it?",
    "dsa": "What is the time and space complexity, and what input breaks it?",
    "web": "How does this behave on a slow connection or a small screen?",
    "ml": "How would you know the model is getting stale in production?",
    "cs": "What happens under the hood when load suddenly spikes 100x?",
    "behavioral": "What was the measurable outcome, and what would you repeat?",
    "project": "If you had two more weeks, what would you build next, and why that order?",
}

SESSION_MIN, SESSION_MAX = 3, 8


def start_session(conn: sqlite3.Connection, category: str | None = None,
                  length: int = 5) -> dict:
    """Start a mock session: a fixed run of distinct questions, answered in
    order, with a review at the end. The whole flow works offline."""
    if not SESSION_MIN <= length <= SESSION_MAX:
        raise ValueError(f"session length must be {SESSION_MIN}-{SESSION_MAX}")
    if category:
        rows = db.query(conn, "SELECT * FROM question WHERE category = ?", (category,))
    else:
        rows = db.query(conn, "SELECT * FROM question")
    if not rows:
        raise ValueError("no questions available")
    recent = db.query(
        conn,
        "SELECT question_id FROM practice_session ORDER BY created_at DESC LIMIT 10")
    recent_ids = {r["question_id"] for r in recent if r["question_id"] is not None}
    fresh = [r for r in rows if r["id"] not in recent_ids]
    pool = fresh if len(fresh) >= length else rows
    chosen = random.sample(pool, min(length, len(pool)))
    session_id = db.insert(conn, "mock_session", {
        "category": category or "", "length": len(chosen),
        "status": "active", "created_at": db.now(), "finished_at": None})
    for pos, q in enumerate(chosen, start=1):
        db.insert(conn, "mock_session_item", {
            "session_id": session_id, "question_id": q["id"],
            "position": pos, "practice_session_id": None})
    db.record_agent_run(
        conn, "mock_session",
        {"category": category or "mixed", "length": len(chosen)},
        [{"step": "compose",
          "detail": f"Picked {len(chosen)} distinct questions"
                    + (f" in category '{category}'" if category else " across all categories")
                    + (", skipping the 10 most recently practiced" if fresh is pool and fresh else ".")}],
        {"session_id": session_id})
    return session_state(conn, session_id)


def session_state(conn: sqlite3.Connection, session_id: int) -> dict:
    s = db.get(conn, "mock_session", session_id)
    if not s:
        raise KeyError(f"session {session_id} not found")
    items = db.query(conn, """
        SELECT i.id AS item_id, i.position, i.practice_session_id,
               q.id AS question_id, q.category, q.difficulty, q.question, q.source
        FROM mock_session_item i LEFT JOIN question q ON q.id = i.question_id
        WHERE i.session_id = ? ORDER BY i.position""", (session_id,))
    answered = [i for i in items if i["practice_session_id"] is not None]
    current = next((i for i in items if i["practice_session_id"] is None), None)
    state = {
        "session_id": session_id, "status": s["status"],
        "category": s["category"] or "mixed", "total": len(items),
        "answered": len(answered),
        "current": current,
    }
    if s["status"] == "finished":
        state["review"] = session_review(conn, session_id)
    return state


def answer_in_session(conn: sqlite3.Connection, session_id: int, question_id: int,
                      answer_text: str, self_rating: int | None) -> dict:
    """Record an answer for the session's current question and advance."""
    s = db.get(conn, "mock_session", session_id)
    if not s:
        raise KeyError(f"session {session_id} not found")
    if s["status"] != "active":
        raise ValueError("session is already finished")
    item = db.query(conn, """
        SELECT * FROM mock_session_item
        WHERE session_id = ? AND question_id = ? AND practice_session_id IS NULL""",
        (session_id, question_id))
    if not item:
        raise ValueError("question is not awaiting an answer in this session")
    result = record_session(conn, question_id, answer_text, self_rating, mode="mock")
    conn.execute(
        "UPDATE mock_session_item SET practice_session_id = ? WHERE id = ?",
        (result["session_id"], item[0]["id"]))
    conn.commit()
    q = result["question"] or {}
    followup = FOLLOWUPS.get(q.get("category", ""), "What would you do differently with one more attempt?")
    state = session_state(conn, session_id)
    out = {
        "review": {
            "question": q.get("question", ""),
            "model_points": q.get("model_points", []),
            "self_rating": self_rating,
            "followup": followup,
        },
        "session": state,
    }
    if state["current"] is None:
        finish_session(conn, session_id)
        out["session"] = session_state(conn, session_id)
    return out


def finish_session(conn: sqlite3.Connection, session_id: int) -> dict:
    conn.execute(
        "UPDATE mock_session SET status = 'finished', finished_at = ? WHERE id = ?",
        (db.now(), session_id))
    conn.commit()
    review = session_review(conn, session_id)
    db.record_agent_run(
        conn, "mock_session",
        {"session_id": session_id, "phase": "review"},
        [{"step": "review",
          "detail": f"Session finished: {review['answered']} questions, "
                    f"average self-rating {review['avg_rating']:.1f}/5."
                    if review["avg_rating"] is not None else
                    f"Session finished: {review['answered']} questions, no self-ratings."}],
        {"avg_rating": review["avg_rating"]})
    return review


def session_review(conn: sqlite3.Connection, session_id: int) -> dict:
    rows = db.query(conn, """
        SELECT i.position, q.question, q.category, s.self_rating
        FROM mock_session_item i
        LEFT JOIN question q ON q.id = i.question_id
        LEFT JOIN practice_session s ON s.id = i.practice_session_id
        WHERE i.session_id = ? ORDER BY i.position""", (session_id,))
    ratings = [r["self_rating"] for r in rows if r["self_rating"] is not None]
    avg = sum(ratings) / len(ratings) if ratings else None
    if avg is None:
        note = "Session recorded. Rate yourself next time - honest ratings make the progress chart real."
    elif avg >= 4:
        note = "Strong session. Keep this bar and raise difficulty or speed next."
    elif avg >= 2.5:
        note = "Solid base. Re-attempt the low-rated questions in a day or two - spaced repetition sticks."
    else:
        note = "You found the gaps - that is the point. Review the model points and retry these soon."
    return {
        "answered": len([r for r in rows if r["self_rating"] is not None or r["question"]]),
        "avg_rating": round(avg, 2) if avg is not None else None,
        "note": note,
        "items": rows,
    }


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Recent practice attempts, newest first, for the progress view."""
    return db.query(conn, """
        SELECT s.id, s.mode, s.self_rating, s.created_at,
               q.question, q.category, q.difficulty
        FROM practice_session s LEFT JOIN question q ON q.id = s.question_id
        ORDER BY s.created_at DESC LIMIT ?""", (limit,))

