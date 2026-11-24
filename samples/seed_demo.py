"""Seed the database with a FICTIONAL demo persona.

Used only for the public demo deployment. Every person, company, project and
application below is invented for demonstration. Run:

    ASCENT_DEMO=1 python samples/seed_demo.py
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.agent import interview

TODAY = dt.date.today()
ISO = lambda d: d.isoformat()

FIELDS = {
    "name": "Aarav Mehta (sample persona)",
    "headline": "Sample: CS final-year student, full-stack + AI/ML",
    "summary": "Sample candidate. Final-year computer science student who builds "
               "Python backends, React frontends and small ML tools. Looking for "
               "SDE internships.",
}

SKILLS = [
    ("Python", "confident", "Sample project: NoteNest"),
    ("FastAPI", "working", "Sample project: NoteNest"),
    ("SQL", "working", "Sample project: NoteNest"),
    ("React", "working", "Sample project: FestFlow"),
    ("JavaScript", "working", "Sample project: FestFlow"),
    ("Machine Learning", "learning", "sample coursework"),
    ("Docker", "learning", "sample coursework"),
    ("Git", "working", "sample projects"),
]

PROJECTS = [
    {"title": "NoteNest (sample)",
     "description": "A fictional note-taking API with full-text search and tags.",
     "tech": ["Python", "FastAPI", "SQLite"], "url": "",
     "highlights": ["Designed the schema", "Wrote the search endpoint"]},
    {"title": "FestFlow (sample)",
     "description": "A fictional college-fest event scheduler with a React UI.",
     "tech": ["React", "JavaScript", "FastAPI"], "url": "",
     "highlights": ["Built the calendar UI", "Deployed on Render"]},
]

APPLICATIONS = [
    {"company": "Sample Corp", "role": "SDE Intern", "status": "applied",
     "applied_on": ISO(TODAY - dt.timedelta(days=9)), "follow_up_due": None,
     "source": "LinkedIn", "url": "", "jd_text":
     "Sample Corp is hiring an SDE Intern with Python, SQL and React. "
     "Docker and AWS are a plus. Full-time hybrid, Bangalore."},
    {"company": "Demo Technologies", "role": "Backend Intern", "status": "saved",
     "deadline": ISO(TODAY + dt.timedelta(days=2)),
     "source": "referral", "url": "", "jd_text":
     "Demo Technologies needs a backend intern: Python, FastAPI, PostgreSQL, "
     "Redis, Docker and CI/CD. Internship, remote."},
]

INTERVIEWS = [
    {"company": "Sample Corp", "role": "SDE Intern", "kind": "technical",
     "scheduled_at": ISO(TODAY + dt.timedelta(days=1)) + "T11:00:00"},
]


def main():
    conn = db.connect()
    db.init_db(conn)
    if db.query(conn, "SELECT COUNT(*) AS c FROM application")[0]["c"]:
        print("demo data already present, skipping")
        return
    for k, v in FIELDS.items():
        conn.execute("INSERT OR REPLACE INTO profile_field (key, value) VALUES (?, ?)", (k, v))
    for name, level, evidence in SKILLS:
        conn.execute("INSERT OR REPLACE INTO skill (name, level, evidence) VALUES (?, ?, ?)",
                     (name, level, evidence))
    for p in PROJECTS:
        db.insert(conn, "project", {
            "title": p["title"], "description": p["description"],
            "tech": str(p["tech"]).replace("'", '"'), "url": p["url"],
            "highlights": str(p["highlights"]).replace("'", '"')})
    now = db.now()
    for a in APPLICATIONS:
        db.insert(conn, "application", {**a, "notes": "sample entry",
                                        "created_at": now, "updated_at": now})
    for iv in INTERVIEWS:
        db.insert(conn, "interview_event", {**iv, "status": "upcoming",
                                            "outcome": "", "notes": "sample interview",
                                            "application_id": None, "created_at": now})
    conn.commit()
    interview.seed_questions(conn)
    interview.regenerate_project_questions(conn)
    # Fictional practice history so the progress view has sample content.
    bank_qs = db.query(conn, "SELECT id FROM question WHERE source = 'bank' LIMIT 3")
    sample_answers = [
        (bank_qs[0]["id"], "practice", "Sample answer: walked through the approach, named the trade-offs, gave a small example.", 4, now - 3 * 86400),
        (bank_qs[1]["id"], "mock", "Sample answer: defined the terms, compared the two options, admitted what I would verify.", 3, now - 2 * 86400),
        (bank_qs[2]["id"], "practice", "Sample answer: solved the easy case first, then generalised; stated the complexity.", 4, now - 1 * 86400),
    ]
    for qid, mode, text, rating, ts in sample_answers:
        db.insert(conn, "practice_session", {
            "question_id": qid, "mode": mode, "answer_text": text,
            "self_rating": rating, "llm_feedback": "", "created_at": ts})
    conn.commit()
    print("demo persona seeded (all data fictional)")


if __name__ == "__main__":
    main()
