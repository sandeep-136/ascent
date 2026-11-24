"""SQLite storage for Ascent.

Private-by-default: every row belongs to the single owner of the deployment.
There is no endpoint that enumerates another user's data because there is no
multi-user model at all - one database, one owner. The public demo deployment
ships with a fictional sample persona instead.

The schema is deliberately plain SQL so every query can be shown and
explained: applications, follow-ups, interviews, prep tasks, practice
sessions, JD analyses and agent runs are all real tables.
"""

import json
import os
import sqlite3
import time

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ascent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_field (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS skill (
    name TEXT PRIMARY KEY,
    level TEXT NOT NULL DEFAULT 'learning',   -- learning | working | confident
    evidence TEXT NOT NULL DEFAULT ''         -- where the skill was used (project/experience)
);

CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tech TEXT NOT NULL DEFAULT '[]',          -- JSON list of technologies
    url TEXT NOT NULL DEFAULT '',
    highlights TEXT NOT NULL DEFAULT '[]'     -- JSON list of talking points
);

CREATE TABLE IF NOT EXISTS application (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',          -- where the posting was found
    status TEXT NOT NULL DEFAULT 'saved',     -- saved|applied|oa|interview|offer|rejected|withdrawn
    applied_on TEXT,                          -- ISO date
    deadline TEXT,                            -- ISO date, application due
    follow_up_due TEXT,                       -- ISO date
    jd_text TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES application(id) ON DELETE SET NULL,
    company TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'technical',   -- phone|oa|technical|hr|other
    scheduled_at TEXT NOT NULL,               -- ISO datetime
    status TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming|done|cancelled
    outcome TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'custom',      -- application|followup|interview_prep|study|custom
    ref_kind TEXT NOT NULL DEFAULT '',        -- application|interview_event|jd_analysis|''
    ref_id INTEGER,
    due_date TEXT,                            -- ISO date
    status TEXT NOT NULL DEFAULT 'open',      -- open|done|skipped
    score REAL NOT NULL DEFAULT 0,            -- planner priority at generation time
    why TEXT NOT NULL DEFAULT '',             -- planner explanation shown to the user
    source TEXT NOT NULL DEFAULT 'planner',   -- planner|user
    created_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,                   -- python|sql|dsa|web|ml|behavioral|project|cs
    difficulty TEXT NOT NULL DEFAULT 'medium',
    tags TEXT NOT NULL DEFAULT '[]',          -- JSON list
    question TEXT NOT NULL,
    model_points TEXT NOT NULL DEFAULT '[]',  -- JSON list of points a strong answer covers
    source TEXT NOT NULL DEFAULT 'bank'       -- bank | generated (from user projects)
);

CREATE TABLE IF NOT EXISTS practice_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER REFERENCES question(id) ON DELETE SET NULL,
    mode TEXT NOT NULL DEFAULT 'practice',    -- practice|mock
    answer_text TEXT NOT NULL DEFAULT '',
    self_rating INTEGER,                      -- 1-5, self assessed
    llm_feedback TEXT NOT NULL DEFAULT '',    -- filled only when an LLM is configured
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mock_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT '',      -- '' means mixed
    length INTEGER NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'active',  -- active|finished
    created_at REAL NOT NULL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS mock_session_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES mock_session(id) ON DELETE CASCADE,
    question_id INTEGER REFERENCES question(id) ON DELETE SET NULL,
    position INTEGER NOT NULL,
    practice_session_id INTEGER REFERENCES practice_session(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS jd_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES application(id) ON DELETE SET NULL,
    jd_text TEXT NOT NULL,
    extracted TEXT NOT NULL DEFAULT '{}',     -- JSON: skills/experience/education/etc from the JD
    matched TEXT NOT NULL DEFAULT '[]',       -- JSON: profile skills the JD asks for
    missing TEXT NOT NULL DEFAULT '[]',       -- JSON: JD skills absent from the profile
    similarity REAL,                          -- embedding cosine JD vs profile summary, 0..1
    suggestions TEXT NOT NULL DEFAULT '[]',   -- JSON prep suggestions
    llm_used INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- plan|fit|interview_feedback
    input TEXT NOT NULL DEFAULT '{}',
    steps TEXT NOT NULL DEFAULT '[]',         -- JSON visible trace of what the agent did
    output TEXT NOT NULL DEFAULT '{}',
    llm_used INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_status ON task(status, due_date);
CREATE INDEX IF NOT EXISTS idx_application_status ON application(status);
CREATE INDEX IF NOT EXISTS idx_interview_sched ON interview_event(status, scheduled_at);
"""


def connect(db_path: str | None = None):
    """Open the app's database. Turso (libSQL over HTTP) when its env is set -
    that is the durable private deployment - else a local SQLite file."""
    from . import turso
    if turso.configured():
        return turso.connect()
    path = db_path or os.environ.get("ASCENT_DB", DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def now() -> float:
    return time.time()


# ---------- generic helpers ----------

def insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    conn.commit()
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int, values: dict) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])
    conn.commit()


def get(conn: sqlite3.Connection, table: str, row_id: int) -> dict | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def delete(conn: sqlite3.Connection, table: str, row_id: int) -> None:
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    conn.commit()


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def record_agent_run(conn: sqlite3.Connection, kind: str, input_data: dict,
                     steps: list, output: dict, llm_used: bool = False) -> int:
    """Persist the visible agent trace: what it was asked, what it did, what it produced."""
    return insert(conn, "agent_run", {
        "kind": kind,
        "input": json.dumps(input_data, ensure_ascii=False),
        "steps": json.dumps(steps, ensure_ascii=False),
        "output": json.dumps(output, ensure_ascii=False),
        "llm_used": 1 if llm_used else 0,
        "created_at": now(),
    })
