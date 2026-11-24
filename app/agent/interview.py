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
