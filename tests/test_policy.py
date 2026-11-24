import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import policy


def p(mode, terms=None):
    return policy.Policy(mode=mode, pii_terms=terms or [])


def test_clean_text_allowed():
    out = policy.guard("You are a coach.", "Explain SQL joins.", p("block"))
    assert out["action"] == "allow" and not out["findings"]


def test_block_mode_raises_on_pii_term():
    pol = p("block", ["Sandeep Sagar", "HKBK College"])
    try:
        policy.guard("system", "I study at HKBK College.", pol)
        assert False, "should block"
    except policy.LLMPrivacyBlock as e:
        assert e.findings


def test_block_mode_raises_on_email_and_phone():
    pol = p("block")
    for text in ["reach me at sandeep@example.com", "call 7338654416 anytime"]:
        try:
            policy.guard("s", text, pol)
            assert False, f"should block: {text}"
        except policy.LLMPrivacyBlock:
            pass


def test_redact_mode_sanitizes():
    pol = p("redact", ["Sandeep Sagar"])
    out = policy.guard("s", "I am Sandeep Sagar, email sandeep@example.com, phone 7338654416.", pol)
    assert out["action"] == "redact"
    assert "Sandeep Sagar" not in out["user"]
    assert "sandeep@example.com" not in out["user"]
    assert "7338654416" not in out["user"]


def test_allow_mode_passes_with_findings():
    pol = p("allow", ["Sandeep Sagar"])
    out = policy.guard("s", "Sandeep Sagar here", pol)
    assert out["action"] == "allow" and out["findings"]


def test_env_loader_defaults_to_block(monkeypatch=None):
    os.environ.pop("LLM_PRIVACY_MODE", None)
    os.environ.pop("LLM_PII_TERMS", None)
    pol = policy.load_policy()
    assert pol.mode == "block"
    os.environ["LLM_PRIVACY_MODE"] = "redact"
    os.environ["LLM_PII_TERMS"] = '["Acme Corp"]'
    pol = policy.load_policy()
    assert pol.mode == "redact" and pol.pii_terms == ["Acme Corp"]
    os.environ.pop("LLM_PRIVACY_MODE", None)
    os.environ.pop("LLM_PII_TERMS", None)
