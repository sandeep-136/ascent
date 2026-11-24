import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.agent import interview

import pytest


def fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    interview.seed_questions(conn)
    return conn


def test_session_start_picks_distinct_questions():
    conn = fresh_conn()
    state = interview.start_session(conn, category="dsa", length=4)
    assert state["total"] == 4 and state["answered"] == 0
    assert state["status"] == "active"
    assert state["current"]["category"] == "dsa"
    ids = [i["question_id"] for i in [state["current"]] ]
    # all items distinct
    rows = db.query(conn, "SELECT question_id FROM mock_session_item WHERE session_id = ?",
                    (state["session_id"],))
    assert len({r["question_id"] for r in rows}) == 4


def test_session_length_bounds():
    conn = fresh_conn()
    with pytest.raises(ValueError):
        interview.start_session(conn, length=2)
    with pytest.raises(ValueError):
        interview.start_session(conn, length=9)


def test_session_answer_advances_and_finishes_with_review():
    conn = fresh_conn()
    state = interview.start_session(conn, length=3)
    sid = state["session_id"]
    ratings = [5, 3, 2]
    for i, rating in enumerate(ratings):
        cur = interview.session_state(conn, sid)["current"]
        out = interview.answer_in_session(conn, sid, cur["question_id"], f"answer {i}", rating)
        assert out["review"]["model_points"]
        assert out["review"]["followup"]
    final = out["session"]
    assert final["status"] == "finished"
    assert final["current"] is None
    review = final["review"]
    assert review["answered"] == 3
    assert abs(review["avg_rating"] - (10 / 3)) < 0.01
    assert review["note"]
    assert len(review["items"]) == 3
    # agent runs recorded for transparency
    runs = db.query(conn, "SELECT kind FROM agent_run WHERE kind = 'mock_session'")
    assert len(runs) >= 2


def test_session_rejects_double_answer_and_unknown():
    conn = fresh_conn()
    state = interview.start_session(conn, length=3)
    sid = state["session_id"]
    qid = state["current"]["question_id"]
    interview.answer_in_session(conn, sid, qid, "first answer", 4)
    with pytest.raises(ValueError):
        interview.answer_in_session(conn, sid, qid, "again", 4)
    with pytest.raises(KeyError):
        interview.session_state(conn, 99999)
    with pytest.raises(ValueError):
        interview.answer_in_session(conn, sid, 99999, "nope", 3)


def test_history_newest_first():
    conn = fresh_conn()
    q = interview.pick_question(conn)
    interview.record_session(conn, q["id"], "older answer", 3)
    interview.record_session(conn, q["id"], "newer answer", 5)
    rows = interview.history(conn)
    assert len(rows) == 2
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    assert rows[0]["question"] and rows[0]["category"]


def test_followups_cover_every_bank_category():
    conn = fresh_conn()
    cats = {r["category"] for r in db.query(conn, "SELECT DISTINCT category FROM question")}
    assert cats <= set(interview.FOLLOWUPS), f"missing followups for {cats - set(interview.FOLLOWUPS)}"
