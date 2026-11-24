"""Ascent evaluations - run with: python evals/run_evals.py

What this measures, on a small fixed fixture:
  1. JD skill extraction: precision/recall of the transparent phrase matcher
     against hand-labeled job descriptions.
  2. Planner ordering: interviews outrank deadlines outrank follow-ups.
  3. Fit engine: matched/missing separation on a known profile.

What this does NOT measure: LLM answer quality (needs a configured provider
and its own labeled set), real-user outcomes, or anything beyond these
fixtures.

Fixture limits, stated plainly: 5 hand-labeled cases total. 3 were written
before the lexicon fixes and their annotations were completed after false
positives were inspected; 2 were added after the fixes and are reported
separately as held_out, but all 5 were labeled by the same person who wrote
the matcher. These are exploratory regression guards for development, not an
independent validation set, and they support no performance claim about the
extractor beyond these cases.
"""

import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.agent import fit, planner
from app.nlp import skills as skill_lex

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jd_fixtures.json")


def fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    return conn


def _score(cases):
    tp = fp = fn = 0
    for case in cases:
        found = set(skill_lex.flat_skills(skill_lex.extract_skills(case["jd"])))
        expected = set(case["expected_skills"])
        tp += len(found & expected)
        fp += len(found - expected)
        fn += len(expected - found)
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    return {"cases": len(cases), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3), "recall": round(recall, 3)}


def eval_extraction(fixture_doc):
    cases = fixture_doc["cases"]
    main = _score([c for c in cases if not c.get("held_out")])
    held = _score([c for c in cases if c.get("held_out")])
    total = _score(cases)
    return {"main": main, "held_out": held, "total": total}


def eval_planner_ordering():
    conn = fresh_conn()
    today = dt.date(2026, 11, 24)
    db.insert(conn, "interview_event", {
        "company": "A", "role": "r", "kind": "technical",
        "scheduled_at": (today + dt.timedelta(days=1)).isoformat() + "T10:00:00",
        "status": "upcoming", "created_at": db.now()})
    db.insert(conn, "application", {
        "company": "B", "role": "r", "status": "saved",
        "deadline": (today + dt.timedelta(days=2)).isoformat(),
        "created_at": db.now(), "updated_at": db.now()})
    db.insert(conn, "application", {
        "company": "C", "role": "r", "status": "applied",
        "applied_on": (today - dt.timedelta(days=10)).isoformat(),
        "follow_up_due": today.isoformat(),
        "created_at": db.now(), "updated_at": db.now()})
    plan = planner.build_plan(conn, today)
    kinds = [i["kind"] for i in plan["items"]]
    ok = kinds.index("interview_prep") < kinds.index("application") < kinds.index("followup")
    return {"order": kinds[:3], "ordering_correct": ok}


def eval_fit_separation():
    conn = fresh_conn()
    db.insert(conn, "skill", {"name": "Python", "level": "confident", "evidence": "sample"})
    db.insert(conn, "skill", {"name": "React", "level": "working", "evidence": "sample"})
    jd = ("Looking for an intern with Python, React, Kubernetes and Terraform. "
          "FastAPI experience preferred. Full-time remote role.")
    result = fit.analyze(conn, jd)
    matched = {m["skill"] for m in result["matched"]}
    missing = set(result["missing"])
    return {
        "matched_ok": {"Python", "React"} <= matched,
        "missing_ok": {"Kubernetes", "Terraform"} <= missing,
        "similarity_present": result["similarity"] is not None,
        "matched": sorted(matched), "missing": sorted(missing),
    }


def main():
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        fixtures = json.load(fh)
    results = {
        "fixture_notice": __doc__.strip().splitlines()[0],
        "annotation_notes": fixtures.get("annotation_notes", []),
        "extraction": eval_extraction(fixtures),
        "planner": eval_planner_ordering(),
        "fit": eval_fit_separation(),
    }
    print(json.dumps(results, indent=2))
    ok = (results["extraction"]["total"]["precision"] >= 0.9
          and results["extraction"]["total"]["recall"] >= 0.9
          and results["planner"]["ordering_correct"]
          and results["fit"]["matched_ok"] and results["fit"]["missing_ok"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
