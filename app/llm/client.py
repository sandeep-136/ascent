"""LLM provider abstraction with graceful degradation.

Three modes:
  * server key (owner): provider from env ("gnani" or "openai"-compatible),
    guarded by daily rate limits and token caps;
  * visitor key: OpenAI-compatible credentials sent with a single request,
    used once and never stored or logged;
  * none: LLM features report themselves as unconfigured instead of
    returning canned text. No fake output, ever.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


class LLMUnavailable(Exception):
    """Raised when no usable LLM credentials exist for a request."""


class LLMRateLimited(Exception):
    """Raised when the server-key budget for the day is exhausted."""


@dataclass
class LLMConfig:
    provider: str
    api_key: str
    base_url: str = ""
    model: str = ""
    max_tokens: int = 700


def _server_config() -> LLMConfig | None:
    provider = os.environ.get("LLM_PROVIDER", "gnani").lower()
    if provider == "gnani":
        key = os.environ.get("GNANI_API_KEY", "")
        if not key:
            return None
        return LLMConfig(provider="gnani", api_key=key,
                         max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "700")))
    if provider == "openai":
        key = os.environ.get("LLM_API_KEY", "")
        base = os.environ.get("LLM_BASE_URL", "")
        model = os.environ.get("LLM_MODEL", "")
        if not (key and base and model):
            return None
        return LLMConfig(provider="openai", api_key=key, base_url=base, model=model,
                         max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "700")))
    return None


class _DailyBudget:
    """In-memory per-process daily counters. On a single-instance free
    deployment this is the hard cap; it resets on restart, which errs on the
    side of the owner's account only downward (limits re-apply immediately)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._day = time.strftime("%Y-%m-%d")
        self._global = 0
        self._per_ip: dict[str, int] = {}

    def _roll(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._global, self._per_ip = today, 0, {}

    def check_and_consume(self, ip: str, per_ip_limit: int, global_limit: int) -> None:
        with self._lock:
            self._roll()
            if self._global >= global_limit:
                raise LLMRateLimited("The shared daily LLM budget for this demo is used up.")
            if self._per_ip.get(ip, 0) >= per_ip_limit:
                raise LLMRateLimited("Daily LLM limit reached for this visitor.")
            self._global += 1
            self._per_ip[ip] = self._per_ip.get(ip, 0) + 1


_budget = _DailyBudget()


def server_llm_available() -> bool:
    return _server_config() is not None


def _complete_gnani(cfg: LLMConfig, system: str, user: str) -> str:
    from gnani.llm import GnaniLLMClient  # gnani-vachana[llm]

    client = GnaniLLMClient(api_key=cfg.api_key)
    completion = client.chat.completions.create(
        model="evon-v3.5",
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=cfg.max_tokens,
        reasoning_effort="none",
    )
    return (completion.choices[0].message.content or "").strip()


def _complete_openai(cfg: LLMConfig, system: str, user: str) -> str:
    """Call any OpenAI-compatible /chat/completions endpoint with requests."""
    import requests

    base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json={"model": cfg.model,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "max_tokens": cfg.max_tokens},
        timeout=60,
    )
    if resp.status_code == 429:
        raise LLMRateLimited("Provider free quota exhausted; try again tomorrow.")
    if resp.status_code >= 400:
        raise LLMUnavailable(f"Provider error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


def complete(system: str, user: str, *, ip: str = "unknown",
             visitor_base_url: str | None = None, visitor_api_key: str | None = None,
             visitor_model: str | None = None) -> str:
    """One LLM completion. Visitor key wins when supplied; otherwise the
    rate-limited server key. Raises LLMUnavailable/LLMRateLimited.

    Every call passes the privacy policy guard first (app.llm.policy):
    in block mode a call carrying detected personal data raises
    LLMPrivacyBlock; in redact mode it is sanitised before sending."""
    from . import policy as _policy
    guarded = _policy.guard(system, user)
    system, user = guarded["system"], guarded["user"]
    if visitor_api_key and visitor_base_url and visitor_model:
        cfg = LLMConfig(provider="openai", api_key=visitor_api_key,
                        base_url=visitor_base_url, model=visitor_model, max_tokens=700)
        return _complete_openai(cfg, system, user)

    if os.environ.get("ASCENT_DEMO") == "1":
        # Public demo deployments never spend a provider key, even if one is
        # configured in the environment by mistake.
        raise LLMUnavailable("LLM is disabled on the public demo.")
    cfg = _server_config()
    if cfg is None:
        raise LLMUnavailable("LLM features are not configured on this deployment.")
    _budget.check_and_consume(
        ip,
        per_ip_limit=int(os.environ.get("LLM_PER_IP_DAILY_LIMIT", "3")),
        global_limit=int(os.environ.get("LLM_GLOBAL_DAILY_LIMIT", "20")),
    )
    if cfg.provider == "gnani":
        return _complete_gnani(cfg, system, user)
    return _complete_openai(cfg, system, user)
