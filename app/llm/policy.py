"""Provider privacy policy guard.

Free LLM tiers (e.g. Gemini's Unpaid Services) state that submitted content
may be reviewed by humans and used to improve products, and that users must
not submit sensitive, confidential or personal information. Consent to use
such a provider does not override that prohibition, so every outbound LLM
call passes through this guard first.

Modes (env LLM_PRIVACY_MODE):
  block   - refuse the call when personal data is detected (DEFAULT)
  redact  - replace detected personal data with placeholders, then send
  allow   - send as-is; only for fictional test data or a provider whose
            terms permit the content

The owner-specific term list comes from env LLM_PII_TERMS (JSON list of
literal strings: name, email, phone, college, employers, private repo names).
It lives in deployment config, never in the repo. Generic patterns (email,
phone, long digit strings) are always checked.

Every guard decision is returned so callers can record it in the agent trace:
a blocked or redacted call is visible, never silent.
"""

import json
import os
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?\d{10}(?!\d)")
LONG_NUMBER_RE = re.compile(r"(?<![\w])\d{9,}(?![\w])")

MODES = ("block", "redact", "allow")


class LLMPrivacyBlock(Exception):
    def __init__(self, findings):
        self.findings = findings
        super().__init__(f"LLM call blocked: personal data detected ({len(findings)} finding(s))")


class Policy:
    def __init__(self, mode: str = "block", pii_terms: list[str] | None = None):
        if mode not in MODES:
            raise ValueError(f"unknown privacy mode: {mode}")
        self.mode = mode
        self.pii_terms = [t for t in (pii_terms or []) if t and len(t) >= 3]


def load_policy() -> Policy:
    mode = os.environ.get("LLM_PRIVACY_MODE", "block").lower()
    try:
        terms = json.loads(os.environ.get("LLM_PII_TERMS", "[]"))
    except json.JSONDecodeError:
        terms = []
    return Policy(mode=mode, pii_terms=terms)


def inspect(text: str, policy: Policy) -> list[dict]:
    """Find personal data in text. Returns a list of {kind, detail} findings."""
    findings = []
    lowered = text.lower()
    for term in policy.pii_terms:
        if term.lower() in lowered:
            findings.append({"kind": "pii_term", "detail": "configured personal term present"})
    if EMAIL_RE.search(text):
        findings.append({"kind": "email", "detail": "email address present"})
    if PHONE_RE.search(text):
        findings.append({"kind": "phone", "detail": "phone-like number present"})
    if LONG_NUMBER_RE.search(text):
        findings.append({"kind": "long_number", "detail": "long numeric identifier present"})
    return findings


def _redact(text: str, policy: Policy) -> str:
    out = text
    for term in sorted(policy.pii_terms, key=len, reverse=True):
        out = re.sub(re.escape(term), "[redacted]", out, flags=re.IGNORECASE)
    out = EMAIL_RE.sub("[redacted-email]", out)
    out = PHONE_RE.sub("[redacted-phone]", out)
    out = LONG_NUMBER_RE.sub("[redacted-number]", out)
    return out


def guard(system: str, user: str, policy: Policy | None = None) -> dict:
    """Apply the policy to an outbound LLM call.

    Returns {"system", "user", "action": "allow"|"redact", "findings": [...]}.
    Raises LLMPrivacyBlock in block mode when findings exist.
    """
    policy = policy or load_policy()
    combined_findings = inspect(system, policy) + inspect(user, policy)
    if not combined_findings:
        return {"system": system, "user": user, "action": "allow", "findings": []}
    if policy.mode == "allow":
        return {"system": system, "user": user, "action": "allow", "findings": combined_findings}
    if policy.mode == "redact":
        return {"system": _redact(system, policy), "user": _redact(user, policy),
                "action": "redact", "findings": combined_findings}
    raise LLMPrivacyBlock(combined_findings)
