"""Safety governor (PLAN.md component 6 / Phase 2).

Keeps the fast cached path safe to run at speed:
- classify MUTATING actions (submit/pay/send/delete/...) that must never be blind-replayed
  without a verification gate, and mint an idempotency key so a retry can't duplicate a
  side effect;
- pace network-visible actions (per-origin concurrency cap + optional human-plausible
  jitter + Retry-After-aware backoff) so going fast locally doesn't trip rate limits / bot
  defenses — speed is won by removing LLM latency, NOT by hammering origins;
- detect anti-bot / CAPTCHA interstitials and escalate to a human rather than burning retries.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import urlsplit

# Keywords marking a step as state-mutating (irreversible side effect).
MUTATING_KEYWORDS = (
    "submit", "pay", "buy", "purchase", "order", "checkout", "send", "delete",
    "remove", "confirm", "transfer", "book", "subscribe", "register", "publish",
    "place order", "sign up", "log out", "sign out", "unsubscribe",
)


def is_mutating(action: str, intent: str = "", name: str = "") -> bool:
    """Heuristic: does this step likely cause an irreversible side effect?"""
    blob = f"{intent} {name}".lower()
    if action == "click":
        return any(k in blob for k in MUTATING_KEYWORDS)
    if action == "press":  # Enter can submit a form
        return any(k in blob for k in MUTATING_KEYWORDS)
    return False  # type/scroll/navigate are not mutating by themselves


def idempotency_key(scope: str, step_index: int, intent: str) -> str:
    basis = f"{scope}|{step_index}|{intent}"
    return "uca-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


# CAPTCHA / anti-bot interstitial signals (substring match on url + title + text).
INTERSTITIAL_SIGNALS = (
    "captcha", "recaptcha", "hcaptcha", "are you a robot", "verify you are human",
    "unusual traffic", "access denied", "checking your browser", "challenge-platform",
    "too many requests", "rate limit", "bot detection", "ddos protection by",
)


def looks_like_interstitial(url: str, title: str, text: str) -> bool:
    blob = f"{url}\n{title}\n{text[:2000]}".lower()
    return any(s in blob for s in INTERSTITIAL_SIGNALS)


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Capped exponential backoff with jitter."""
    return min(cap, base * (2 ** attempt)) + random.uniform(0.0, base)


@dataclass
class PacingGovernor:
    """Per-origin concurrency cap + optional human-plausible jitter + Retry-After backoff.

    Defaults are a no-op (no jitter, high concurrency) so local/deterministic runs stay
    fast; turn on jitter and tighten concurrency for live sites.
    """

    min_action_ms: float = 0.0
    max_action_ms: float = 0.0
    per_origin_concurrency: int = 16
    _sems: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    _retry_after_until: dict[str, float] = field(default_factory=dict)

    def _sem(self, origin: str) -> asyncio.Semaphore:
        sem = self._sems.get(origin)
        if sem is None:
            sem = asyncio.Semaphore(self.per_origin_concurrency)
            self._sems[origin] = sem
        return sem

    def note_retry_after(self, origin: str, seconds: float) -> None:
        self._retry_after_until[origin] = time.monotonic() + max(0.0, seconds)

    @asynccontextmanager
    async def gate(self, origin: str) -> AsyncIterator[None]:
        deadline = self._retry_after_until.get(origin)
        if deadline is not None:
            wait = deadline - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        sem = self._sem(origin)
        await sem.acquire()
        try:
            if self.max_action_ms > 0:
                lo = min(self.min_action_ms, self.max_action_ms)
                hi = max(self.min_action_ms, self.max_action_ms)
                await asyncio.sleep(random.uniform(lo, hi) / 1000.0)
            yield
        finally:
            sem.release()
