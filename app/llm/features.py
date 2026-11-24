"""LLM feature payloads with preview-before-send enforcement.

Contract:
1. Payloads are constructed minimal - only the fields a feature needs, built
   server-side from structured rows. Free-form personal text is never
   attached silently.
2. Nothing is sent without a preview. /api/llm/preview builds the payload,
   runs the privacy guard in report mode, stores the payload, and returns it
   for the user to read. /api/llm/send takes only a payload id; the payload
   is re-read from the store (the client cannot alter it after preview) and
   the guard is re-run in enforce mode before any provider call.
3. Tiers:
   - "structured": payload contains only skill lists and generic role text.
   - "free_text": payload contains user-entered text (e.g. an interview
     answer). Guard findings are shown in the preview, and in block mode any
     finding refuses the send.
"""

import hashlib
import json
import time
import uuid

from .. import db as dbmod
from . import policy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_payload (
    id TEXT PRIMARY KEY,
    feature TEXT NOT NULL,
    tier TEXT NOT NULL,
    system TEXT NOT NULL,
    user TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    guard_report TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'previewed',   -- previewed|sent|blocked
    created_at REAL NOT NULL,
    sent_at REAL
);
"""

SYSTEM_COACH = (
    "You are a concise career-prep coach for a final-year CS student. "
    "Reply in at most 4 short bullets, concrete and specific. "
    "No flattery, no generic advice."
)
SYSTEM_INTERVIEWER = (
    "You are a technical interviewer giving feedback on a practice answer. "
    "Reply with: what was good (1 line), what was missing (up to 3 bullets), "
    "and one stronger phrasing of the weakest point. Be direct and brief."
)


def init(conn) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n\n{user}".encode()).hexdigest()


def build_payload(conn, feature: str, params: dict) -> dict:
    """Construct the minimal payload for a feature. Raises KeyError/ValueError
    on unknown feature or missing source row."""
    if feature == "gap_brief":
        row = dbmod.query(conn, "SELECT * FROM jd_analysis WHERE id = ?",
                          (int(params["analysis_id"]),))
        if not row:
            raise KeyError("analysis not found")
        row = row[0]
        missing = json.loads(row["missing"])
        matched = json.loads(row["matched"])
        user = (
            f"Job description asks for: {', '.join(missing + matched)}.\n"
            f"Candidate profile already covers: {', '.join(matched) or 'none listed'}.\n"
            f"Gaps to prepare: {', '.join(missing) or 'none'}.\n"
            "Give a short prep brief for the gaps."
        )
        return {"feature": feature, "tier": "structured",
                "system": SYSTEM_COACH, "user": user}

    if feature == "answer_feedback":
        qrow = dbmod.query(conn, "SELECT * FROM question WHERE id = ?",
                           (int(params["question_id"]),))
        if not qrow:
            raise KeyError("question not found")
        q = qrow[0]
        answer = (params.get("answer") or "").strip()
        if not answer:
            raise ValueError("answer text is required")
        points = json.loads(q["model_points"] or "[]")
        user = (
            f"Interview question: {q['question']}\n"
            f"Key points a strong answer covers: {', '.join(points)}.\n"
            f"Candidate's practice answer:\n{answer}\n"
            "Give feedback on the answer."
        )
        return {"feature": feature, "tier": "free_text",
                "system": SYSTEM_INTERVIEWER, "user": user}

    raise ValueError(f"unknown feature: {feature}")


def preview(conn, feature: str, params: dict) -> dict:
    """Build + store a payload for preview. Never calls a provider."""
    init(conn)
    payload = build_payload(conn, feature, params)
    pol = policy.load_policy()
    findings = (policy.inspect(payload["system"], pol)
                + policy.inspect(payload["user"], pol))
    pid = uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO llm_payload (id, feature, tier, system, user, payload_hash,"
        " guard_report, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, feature, payload["tier"], payload["system"], payload["user"],
         _hash(payload["system"], payload["user"]),
         json.dumps({"mode": pol.mode, "findings": findings}),
         "previewed", time.time()))
    conn.commit()
    return {"payload_id": pid, "feature": feature, "tier": payload["tier"],
            "system": payload["system"], "user": payload["user"],
            "privacy_mode": pol.mode, "findings": findings,
            "blocked_in_current_mode": bool(findings) and pol.mode == "block"}


def send(conn, payload_id: str, *, ip: str = "unknown") -> dict:
    """Send a previously previewed payload. The payload comes from the store,
    never from the client, so what was previewed is exactly what is sent."""
    init(conn)
    rows = dbmod.query(conn, "SELECT * FROM llm_payload WHERE id = ?", (payload_id,))
    if not rows:
        raise KeyError("payload not found")
    row = rows[0]
    if row["status"] == "sent":
        raise ValueError("payload already sent")
    if _hash(row["system"], row["user"]) != row["payload_hash"]:
        raise ValueError("payload integrity check failed")

    # Enforce the privacy policy on the exact stored payload.
    guarded = policy.guard(row["system"], row["user"])

    from . import client
    text = client.complete(guarded["system"], guarded["user"], ip=ip)

    conn.execute("UPDATE llm_payload SET status = 'sent', sent_at = ? WHERE id = ?",
                 (time.time(), payload_id))
    dbmod.insert(conn, "agent_run", {
        "kind": "llm",
        "input": json.dumps({"feature": row["feature"], "payload_id": payload_id,
                             "tier": row["tier"]}),
        "steps": json.dumps([
            {"step": "construct", "detail": f"payload built for {row['feature']}"},
            {"step": "preview", "detail": "exact payload shown before send"},
            {"step": "guard", "detail": f"privacy action: {guarded['action']}"},
            {"step": "send", "detail": "provider call completed"},
        ]),
        "output": json.dumps({"chars": len(text)}),
        "llm_used": 1,
        "created_at": dbmod.now()})
    conn.commit()
    return {"text": text, "privacy_action": guarded["action"]}
