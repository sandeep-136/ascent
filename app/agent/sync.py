"""Keep an application's pipeline status in step with its interview rounds.

Deterministic and conservative:
  * only moves forward through saved -> applied -> oa -> interview
  * a logged "Rejected" / "Offer" outcome closes the application
  * never touches an application already at offer / rejected / withdrawn
  * cancelled rounds and ambiguous outcomes change nothing
Every change is returned with a reason so the UI can show it and the user
can undo it by editing the application.
"""
from .. import db

TERMINAL = {"offer", "rejected", "withdrawn"}
ORDER = {"saved": 0, "applied": 1, "oa": 2, "interview": 3}


def decide(app_status: str, kind: str, status: str, outcome: str):
    """Return (new_status, reason) or None."""
    if app_status in TERMINAL or status == "cancelled":
        return None
    outcome = (outcome or "").strip().lower()
    if status == "done" and outcome == "rejected":
        return "rejected", "Round outcome logged as Rejected."
    if status == "done" and outcome == "offer":
        return "offer", "Round outcome logged as Offer."
    target = "oa" if kind == "oa" else "interview"
    if ORDER.get(app_status, 0) < ORDER[target]:
        label = "online assessment" if target == "oa" else f"{kind} round"
        return target, f"A {label} was added for this application."
    return None


def sync_application(conn, application_id, kind, status, outcome):
    """Apply decide() to the linked application. Returns a change dict or None."""
    if not application_id:
        return None
    row = db.get(conn, "application", application_id)
    if not row:
        return None
    result = decide(row["status"], kind, status, outcome)
    if not result:
        return None
    new_status, reason = result
    db.update(conn, "application", application_id,
              {"status": new_status, "updated_at": db.now()})
    return {"application_id": application_id, "company": row["company"],
            "from": row["status"], "to": new_status, "reason": reason}
