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
import json
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

# ======================================================================================================
#  THE KEYWORD CLASSIFIER IS A BAD SIGNAL. IT IS NOT GETTING FIXED. DO NOT BUILD A REFUSAL ON IT.
# ======================================================================================================
#
# Measured, both directions, on the list below (`_keyword_mutating`, a bare substring test):
#
#     ordinary READ-ONLY controls that classify as MUTATING     18 of 64   (28%)
#     genuine WRITE controls it catches, with no form context    15 of 33   (45%)
#     keywords that are substrings of ordinary words             15 of 20   (75%)
#
# "Show borders" -> `order`. "Sender" -> `send`. "Bookmarks" -> `book`. "Payment history" -> `pay`.
# "Confirmation number" -> `confirm`. Meanwhile "Save", "Apply", "Continue", "Add to cart", "Approve",
# "Issue refund" and "Merge" are all real commits that read as READS.
#
# AND IT CANNOT BE REPAIRED BY A BETTER MATCHING RULE. Measured, three ways:
#
#     rule                        read false-positives fixed    genuine writes that LOSE their gate
#     substring (this one)                     --                             0
#     word boundary                          17/20                        16/21
#     affix + inflection aware                3/20                           0
#
# Word boundaries un-gate ordinary inflections and affixes of real commits — "Reorder", "Resend",
# "Unpublish", "Ordering", "Submitting form" — trading a loud over-refusal for a SILENT under-gate, which
# is the wrong direction on inviolable #3. The safe affix-aware variant keeps every write and removes
# almost nothing, because the surviving false positives are DEVERBAL NOUNS: payment, sender, subscriber,
# publisher, confirmation, transfers, bookings. Those are morphologically identical to the inflected
# verb. "Transfers" (a list you read) versus "Transferring funds" (a commit) is not separable by any
# string rule — it needs part-of-speech or semantics, which is a different project.
#
# SO WHY IS IT STILL HERE? Because it is the ONLY signal available BEFORE acting. `flow.py`'s
# `block_mutations` (the replan path) must refuse to PERFORM a write during a replay-triggered re-author,
# and it has to decide that pre-act — wire evidence is post-hoc by construction, since observing the
# request requires making it. Deleting the keyword fallback would blind that refusal completely and let
# the re-author fire an unapproved write. The recorder's undeclared-write refusal has the same shape.
# It is a bad signal kept for the one job where no better signal can exist.
#
# WHAT THIS MEANS FOR CALLERS — the rule that matters:
#
#     A `mutating` mark is a GUESS, not evidence. It is sound to be conservative because of one
#     (gate it, key it, refuse to re-author it). It is NOT sound to refuse a FLOW because of one.
#
# That distinction is register finding R3.5 and decision D0, and it has already been learned the
# expensive way: a flow-level refusal keyed off this signal was built, passed 105 targeted tests and a
# 24-cell invariant matrix, and would have broken a large population of ordinary read flows whose only
# offered remedies both fail. See `docs/open-defects.md` (R3.5) and `docs/correctness-plan.md` (D0,
# blocked indefinitely). The cache USED to store the mark but not which signal set it, so nothing
# downstream could tell a guess from evidence. Since 0.92.0 it stores both: `CachedStep.mutating_sources`
# records every `MARK_*` below that independently supports the mark — D0's lever (ii), landed standalone
# rather than inside S6/AB-1 as the plan expected.
#
# SINCE 0.93.0 THE FIELD IS TRUST-BEARING: `flows.mark_step` lets a human demote a mark only when every
# recorded source is a GUESS (keyword / caption / their own prior verdict). Evidence — `form_method`,
# `wire` — a precaution (`overgate`), a declaration (`declared`) or an unrecoverable basis (`unknown`)
# all refuse. A new signal added below MUST decide explicitly whether it belongs in
# `flows._DEMOTABLE_MARKS`; the set is an ALLOWLIST, so a new mark is non-demotable by default, which is
# the safe direction. The contract, so a consumer cannot misread it: the list is *every signal that independently supports this mark*, not "the
# one that decided it" — a step a keyword guessed at and the wire then confirmed carries BOTH, and the
# first draft of that field got it backwards, filing a wire-proven commit as a bare guess.
#
# Before touching this list, re-run the measurement. `tests/test_write_classification.py` pins the known
# false positives and false negatives so a well-meaning tightening fails loudly instead of silently
# un-gating a commit.
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
    return classify_mutation_with_source(action, intent, name, ctx)[0]


# The signals that can set a `mutating` mark. They are NOT interchangeable, which is the whole reason
# this exists: `FORM_METHOD` and `WIRE` are EVIDENCE, `KEYWORD` is a guess with a measured 28% false
# positive rate, and `OVERGATE` is a precaution about a step nothing said anything about at all.
# Collapsing them to one bit is what makes D0 unbuildable and R4.27 invisible.
MARK_KEYWORD = "keyword"          # `MUTATING_KEYWORDS` substring hit — a GUESS
MARK_FORM_METHOD = "form_method"  # the target's own form declares a non-idempotent method — EVIDENCE
MARK_WIRE = "wire"                # a non-idempotent request was observed leaving — EVIDENCE, post-hoc
MARK_DECLARED = "declared"        # the human declared `spec.mutate` and the target submits a form
MARK_OVERGATE = "overgate"        # a blanket precaution (AB-1): nothing attributed this step at all
MARK_CAPTION = "caption"          # a keyword hit on an LLM-written caption — a guess about a guess
MARK_HUMAN = "human"              # a human's explicit verdict (`flows.mark_step`) — the only NON-inferred source
MARK_UNKNOWN = "unknown"          # marked before provenance existed: SOMETHING marked it, basis unrecoverable
# D7: the request BODY named a read operation, so a POST that `is_write_request` would call a write
# was not counted as one. It is recorded rather than silently dropped -- stripping the evidence is
# what would make R4.27 invisible again, and this mark is how a demoted step still says WHY.
MARK_BODY_READ = "body_read"


# --------------------------------------------------------------------------- D7: reads over POST
#
# `is_write_request` classifies by HTTP METHOD, so an app that serves reads over POST has every read
# step filed as a write (R4.27). The consequence is not cosmetic: a marked step loses self-heal and
# suffix-replan, and its mutation gate turns ordinary drift into a hard refusal.
#
# THIS IS THE JSON-RPC HALF ONLY, AND THE SCOPE MATTERS. It clears Odoo-style `call_kw` and four
# named page-load routes. It does NOT touch GraphQL, whose reads and mutations share one endpoint and
# whose operation lives in a query string this does not parse -- and GraphQL is the population R4.27
# was originally filed on (12/12). `tests/test_annotation_disposition.py` pins that those controls
# still cache as write flows, and it must keep passing.
#
# THE SHAPE IS AN ALLOWLIST FAILING CLOSED: enumerate the operations known to read, and everything
# else stays a write. That is why the recorded rejection of a URL denylist does not transfer -- a
# GraphQL mutation can travel the same URL as a query, but an Odoo `create` cannot travel under the
# name `search_read`, because the method name IS the operation (`getattr(model, method)`).
#
# THE SET IS OBSERVED, NOT DRAFTED (R4.122). Every entry below was seen live on the corpus substrate.
# Four methods that were also seen live are deliberately ABSENT: `onchange` (the documented
# write-in-a-read-shaped-call hazard), `check_access_rights`, `render_public_asset` and
# `systray_get_activities`/`has_group` -- all genuine reads, none of them needed to clear a step, and
# an allowlist earns entries by necessity rather than by plausibility.
_CALL_KW = "/web/dataset/call_kw"

READ_RPC_METHODS = frozenset({
    "search_read", "read", "search", "search_count", "read_group", "name_search", "fields_get",
    "web_search_read", "web_read", "web_read_group", "load_views", "get_views",
})

# Route-EXACT, never prefix: a prefix match is how an allowlist becomes a hole.
READ_ROUTES = frozenset({
    "/web/webclient/load_menus", "/web/action/load", "/web/webclient/translations",
})


def _path_of(url: str) -> str:
    p = url.split("?", 1)[0].split("#", 1)[0]
    if p.startswith("http://") or p.startswith("https://"):
        rest = p.split("/", 3)
        p = "/" + rest[3] if len(rest) > 3 else "/"
    return p


def body_says_read(url: str, body: Optional[str]) -> bool:
    """Did this POST's own body name a READ operation? Fails CLOSED in every ambiguous case.

    Returns True only for: a `call_kw` route whose ORM method -- read from the URL suffix and the
    JSON-RPC body, which must AGREE when both are present -- is on `READ_RPC_METHODS`; or one of the
    route-exact page-load reads. Everything else is False: unknown methods, batch arrays, non-`call`
    envelopes, unparseable or absent bodies, and any suffix/body disagreement.

    THE DISAGREEMENT CASE IS NOT A PUZZLE TO RESOLVE. Two readings that differ mean the operation is
    ambiguous, and an ambiguous operation is a write.

    MEASURED BEFORE IT WAS WRITTEN (R4.122): per-POST this clears 13 of 22 Odoo requests, but per
    STEP -- the number that actually decides anything, since a step is demoted only when EVERY
    request in its act window is -- it clears 2 of 2, because each interaction causes exactly one
    `web_search_read` and the uncleared remainder is page-load chrome that never lands in an act
    window. A first draft tested `endswith("/call_kw")` and matched NOTHING; the real route is
    `/web/dataset/call_kw/<model>/<method>`.
    """
    if not body:
        return False
    path = _path_of(url)
    if path in READ_ROUTES:
        return True
    if not path.startswith(_CALL_KW):
        return False
    try:
        env = json.loads(body)
    except Exception:  # noqa: BLE001 - an unreadable body names no operation
        return False
    if not isinstance(env, dict) or env.get("method") != "call":
        return False          # a batch array is a list, and lands here too
    params = env.get("params")
    from_body = params.get("method") if isinstance(params, dict) else None
    tail = path[len(_CALL_KW):].strip("/").split("/")
    from_url = tail[1] if len(tail) > 1 else None
    if from_body is not None and from_url is not None and from_body != from_url:
        return False
    method = from_body or from_url
    return method in READ_RPC_METHODS


def classify_mutation_with_source(action: str, intent: str = "", name: str = "",
                                  ctx: Optional[dict] = None) -> tuple[bool, str]:
    """`classify_mutation`, plus WHICH signal decided it. ONE implementation, two surfaces — a second
    transcription of this ladder is precisely the defect R3.1 was filed for.

    The source is meaningful only when the verdict is True; a False verdict returns `""` because there
    is no mark to explain. Callers that need provenance use this; `classify_mutation` stays a bare bool
    so the four existing call sites and the public `is_mutating` shim are untouched.
    """
    ctx = ctx or {}
    if action == "click":
        method = (ctx.get("form_method") or "").lower()
        if ctx.get("submit") and method:        # a real form submit -> the method is decisive
            hit = method in NONIDEMPOTENT_METHODS
            return hit, (MARK_FORM_METHOD if hit else "")
        hit = _keyword_mutating(intent, name)   # JS button / non-submit -> keyword fallback
        return hit, (MARK_KEYWORD if hit else "")
    if action == "press":  # Enter can submit a form; without the focused element's form, use keywords
        hit = _keyword_mutating(intent, name)
        return hit, (MARK_KEYWORD if hit else "")
    return False, ""  # type/scroll/navigate are not mutating by themselves


def is_mutating(action: str, intent: str = "", name: str = "") -> bool:
    """Keyword-only classification (no DOM context) — a back-compat shim over `classify_mutation`."""
    return classify_mutation(action, intent, name, None)


def idempotency_key(scope: str, step_index: int, intent: str, *, slot_values: Optional[dict] = None) -> str:
    """Stable dedupe key for a write. The base (scope, step_index, intent) is run-INVARIANT so a retry of
    the SAME write dedupes. `slot_values` (H3 typed templates) adds a payload channel: two rows of a
    parameterized write with DIFFERENT values mint DIFFERENT keys (so a backend dedupe layer can't
    silently drop rows 2..N), while the SAME row on retry mints the SAME key — canonicalized (sorted keys,
    str() values) so the digest can't wobble across runs and double-write. None/{} => the base key,
    byte-identical to before (existing single-write flows are unchanged)."""
    basis = f"{scope}|{step_index}|{intent}"
    if slot_values:
        # INJECTIVE serialization: JSON-encode the sorted (name, str(value)) pairs. A naive
        # "|".join(f"{k}={v}") is many-to-one when a free-text value contains the '|'/'=' delimiters —
        # two DISTINCT rows could then collapse to ONE basis and mint ONE key, so a backend dedupe would
        # silently drop the second (a suppressed write). JSON escapes the delimiters inside strings, so the
        # row -> basis map is one-to-one. str() keeps the canonicalization stable (2 and "2" agree).
        canon = json.dumps([[k, str(slot_values[k])] for k in sorted(slot_values)],
                           separators=(",", ":"), ensure_ascii=True)
        basis = f"{basis}|slots:{canon}"
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


def merge_marks(existing: Optional[list[str]], *added: str) -> list[str]:
    """Union of `MARK_*` sources — sorted, deduped, and NEVER dropping one already recorded.

    Union, not replace: R4.2 is what happens when a newer signal overwrites what an older one earned.
    A step a keyword guessed at and the wire later confirmed carries BOTH, because "guessed, then
    confirmed" is a materially different claim from either alone.
    """
    out = set(existing or ())
    out.update(a for a in added if a)
    return sorted(out)
