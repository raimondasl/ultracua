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
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

# Keywords marking a step as state-mutating (irreversible side effect).
MUTATING_KEYWORDS = (
    "submit", "pay", "buy", "purchase", "order", "checkout", "send", "delete",
    "remove", "confirm", "transfer", "book", "subscribe", "register", "publish",
    "place order", "sign up", "log out", "sign out", "unsubscribe",
)

# HTTP methods that are NOT safe/idempotent — a form using one of these is a write.
NONIDEMPOTENT_METHODS = ("post", "put", "delete", "patch")

# Known analytics / telemetry / RUM / error-reporting vendor hosts. A non-idempotent request to one of
# these is a BEACON, not a state-changing write, so write-detection ignores it — otherwise every click on
# an instrumented site would look like a write and best-of-N could never re-sample. Matched as a netloc
# SUFFIX so subdomains (region1.google-analytics.com, o123.ingest.sentry.io) are covered.
#
# Curated for HIGH CONFIDENCE on purpose: each entry is a pure-beacon endpoint that NEVER receives a
# user-initiated write. Hosts that also take real writes (facebook.com posts, an Intercom message send)
# are deliberately LEFT OUT — a missing entry only costs a wasted re-sample (safe), but a wrong entry
# could hide a genuine write and cause a double-submit (unsafe). For the same reason we never denylist by
# PATH (`/events`, `/track`) — those collide with real write endpoints (creating a calendar event POSTs
# to `/events`).
TELEMETRY_HOSTS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com",
    "g.doubleclick.net", "stats.g.doubleclick.net",
    "segment.io", "segmentapis.com",
    "amplitude.com",
    "mixpanel.com",
    "sentry.io",
    "bugsnag.com",
    "nr-data.net", "newrelic.com",                                  # New Relic browser agent (bam.nr-data.net)
    "browser-intake-datadoghq.com", "browser-intake-datadoghq.eu",  # Datadog RUM intake
    "fullstory.com",
    "hotjar.com", "hotjar.io",
    "heap.io", "heapanalytics.com",
    "clarity.ms",                                                   # Microsoft Clarity
    "posthog.com",
    "plausible.io",
    "scorecardresearch.com", "quantserve.com",
    "logrocket.com", "logrocket.io", "lr-ingest.io", "lr-in.com",
    "mouseflow.com",
    "snowplowanalytics.com",
    "cloudflareinsights.com",                                       # Cloudflare Web Analytics beacon
    "bat.bing.com",                                                 # Microsoft UET tag
    "px.ads.linkedin.com",                                          # LinkedIn Insight tag
)


def _keyword_mutating(intent: str, name: str) -> bool:
    blob = f"{intent} {name}".lower()
    return any(k in blob for k in MUTATING_KEYWORDS)


def classify_mutation(action: str, intent: str = "", name: str = "",
                      ctx: Optional[dict] = None) -> bool:
    """Does this step likely cause an irreversible side effect?

    Prefers the target's STRUCTURAL signal over keywords. A click on a form-submit control is judged by
    the form's METHOD — GET is an idempotent read (search / filter), POST/PUT/DELETE/PATCH is a write.
    That catches icon-only / bland-intent submit buttons the keyword list misses, and stops false-firing
    on reads like "submit the search". With no form context (a JS-driven button) it falls back to the
    keyword heuristic. `ctx` is a `{submit: bool, form_method: str}` probe of the target (see
    `snapshot.mutation_context`). `type` / `scroll` / `navigate` are never mutating on their own.
    """
    ctx = ctx or {}
    if action == "click":
        method = (ctx.get("form_method") or "").lower()
        if ctx.get("submit") and method:        # a real form submit -> the method is decisive
            return method in NONIDEMPOTENT_METHODS
        return _keyword_mutating(intent, name)  # JS button / non-submit -> keyword fallback
    if action == "press":  # Enter can submit a form; without the focused element's form, use keywords
        return _keyword_mutating(intent, name)
    return False  # type/scroll/navigate are not mutating by themselves


def is_mutating(action: str, intent: str = "", name: str = "") -> bool:
    """Keyword-only classification (no DOM context) — a back-compat shim over `classify_mutation`."""
    return classify_mutation(action, intent, name, None)


def idempotency_key(scope: str, step_index: int, intent: str) -> str:
    basis = f"{scope}|{step_index}|{intent}"
    return "uca-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def is_telemetry_host(url: str) -> bool:
    """Is this URL's host a known analytics/telemetry/RUM beacon endpoint (see `TELEMETRY_HOSTS`)?

    Suffix-matched on the bare hostname with a dot boundary, so `region1.google-analytics.com` matches
    but `notgoogle-analytics.com` does not."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in TELEMETRY_HOSTS)


def is_write_request(method: str, url: str) -> bool:
    """The network signature of a state-changing write: a non-idempotent request to a non-telemetry host.

    ORIGIN-INDEPENDENT by design — a same-origin form POST and a cross-origin POST to a 3rd-party
    payment/API host are both writes (the latter is the gap a same-origin-only check misses, letting
    best-of-N re-author and double-submit). Beacon-aware so the breadth doesn't false-fire on analytics.
    The CALLER additionally gates on the act window (see `flow._author_steps`) so a background/1st-party
    beacon that isn't on a known vendor host — and so slips past the denylist — still doesn't count unless
    it fired in causal response to an actuated action."""
    return method.upper() in ("POST", "PUT", "PATCH", "DELETE") and not is_telemetry_host(url)


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
