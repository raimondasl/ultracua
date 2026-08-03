"""Define a recurring browser task once, then run it — safely, unattended.

A `FlowSpec` is a named, reusable task (start URL + goal + auth + what data to pull).
- `learn()` LLM-authors the flow (and returns the steps + data to inspect),
- `approve()` marks a verified flow trusted,
- `replay()` reproduces it at 0-LLM navigation, returns the extracted data, and **fails loud**:
  it raises `FlowReplayError` on any drift (no cached flow / unresolved locator / data not found /
  the data's *shape* changed vs the learned run) rather than returning wrong data. With
  `on_drift="relearn"` it re-authors the flow instead of raising. Trust metadata (approval +
  the learned output shape) lives in a `<key>.meta.json` sidecar next to the cached flow.

The product-facing layer over the `run_cached` engine — see ROADMAP.md (Phase A/B).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Optional, Union

from .browser import BrowserSession, _acquire_driver, _release_driver, driver_scope
from .cache import (CacheUnreadableError, CachedFlow, CachedStep, FlowCache, StepConfirm,
                    flow_key, steps_hash)
from .conditions import condition_present
from .config import flow_home, settings
from .contracts import (
    CONTRACT_ATTRS, DELTA_WARMUP, accrue_ring, check_contracts, check_magnitude, effective_contracts,
    magnitude_fields, seed_contracts,
)
from .history import history_path, load_history, save_history, set_anchor
from .extract import extract
from .flow import run_cached
from .ledger import LedgerError, RunLedger
from .locators import resolve
from .obs import get_logger
from .pin import find_pin, read_pin
from .providers import build_router, get_provider
from .recorder import caption_intents, record_demo
from .safety import idempotency_key
from .snapshot import REDACTED

if TYPE_CHECKING:
    from playwright.async_api import Page

_log = get_logger("flows")
# De-dup for the forward-compat "unknown meta field" warning below: warn ONCE per distinct set of
# dropped keys, not on every _load_meta — which runs on the replay / health / run_all / _update_meta
# hot paths, so a legitimately-newer meta would otherwise spam the log several times per flow per cycle.
_warned_unknown_meta_keys: set[frozenset[str]] = set()

# login is either a declarative LoginSpec or an async callable that authenticates a page.
LoginCallable = Callable[["Page"], Awaitable[None]]


@dataclass
class LoginSpec:
    """How to (re)authenticate a flow whose cookie session expires.

    Credentials are read from environment variables **at runtime** (never stored in the spec or
    the cached flow); only the resulting cookies (storage_state) are persisted. The login form is
    filled heuristically (first text/email input + the password input, then Enter) unless explicit
    selectors are given.
    """

    url: str
    username_env: str = "ULTRACUA_USERNAME"
    password_env: str = "ULTRACUA_PASSWORD"
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None  # None -> press Enter in the password field
    # success check (so a failed login can't poison a working session). Default: assume success
    # if we navigated away from `url`. Override for SPA logins that stay on the same URL.
    success_selector: Optional[str] = None       # an element present only once logged in
    success_url_contains: Optional[str] = None   # a substring the post-login URL must contain
    timeout_ms: Optional[int] = None             # per-step timeout for the login form actions


@dataclass
class MutateSpec:
    """Marks a FlowSpec as a WRITE flow (submit/post/purchase) and declares how to know the
    write landed — Phase D's action-completion verification (ROADMAP Phase D).

    A write that can't be confirmed is fire-and-hope, so a mutate flow MUST declare at least one
    `confirm_*` check: after the flow runs, that condition must hold or replay fails loud
    (`FlowReplayError`) — the write is never silently reported as success because a click didn't
    throw. The check mirrors `LoginSpec`'s success-check shape.

    Optional `precheck_*` gives opt-in idempotency for ONE-SHOT writes (don't purchase twice): a
    cheap separate pre-pass visits `precheck_url` (default: the flow's start_url) and, if the
    end-state is already present, the write is skipped and replay reports `already-done`. Leave it
    unset for RECURRING writes (e.g. placing today's order daily) — a state that legitimately
    recurs would otherwise be skipped. There is deliberately NO durable "already committed" ledger.
    """

    # action-completion verification — at least one is required; ANY that holds = confirmed.
    confirm_selector: Optional[str] = None        # element present only once the write committed
    confirm_text_contains: Optional[str] = None   # substring the post-write page text must contain
    confirm_url_contains: Optional[str] = None     # substring the post-write URL must contain
    timeout_ms: Optional[int] = None              # how long to wait for the confirmation to appear
    # opt-in idempotency precheck (one-shot writes only) — see the class docstring.
    precheck_url: Optional[str] = None            # where to look (default: the flow's start_url)
    precheck_selector: Optional[str] = None
    precheck_text_contains: Optional[str] = None
    precheck_url_contains: Optional[str] = None    # already-done state distinguishable only by URL
    # Phase G — MULTI-WRITE: per-write completion barriers, in COMMIT ORDER (one `StepConfirm` per write).
    # When set, each is attached at record time to the Nth mutating step (count-checked; `expects_intent` is
    # required when >1 write to anchor the binding), and replay verifies each write the moment it actuates —
    # an absent->present transition — failing loud and NOT proceeding if one can't be confirmed. `confirm_*`
    # above stays the WHOLE-FLOW / overall check; leave `step_confirms` unset for a single-outcome (Phase-D)
    # write. (Per-write one-shot RESUME is a separate deferred slice — see StepConfirm; until then a
    # multi-write flow re-fires its writes on a re-run and is not auto-retried after auth-refresh.)
    step_confirms: Optional[list[StepConfirm]] = None

    def has_confirm(self) -> bool:
        return any((self.confirm_selector, self.confirm_text_contains, self.confirm_url_contains))

    def has_precheck(self) -> bool:
        return any((self.precheck_selector, self.precheck_text_contains, self.precheck_url_contains))

    def is_multiwrite(self) -> bool:
        """True iff this flow declares more than one per-write barrier (a true multi-write transaction)."""
        return bool(self.step_confirms) and len(self.step_confirms) > 1


@dataclass
class SlotSpec:
    """H3 typed templates: one parameterizable input on a flow. The typed contract a `params={...}` value
    is validated against (0-LLM pre-flight) before any browser action. `type` is a JSON-Schema scalar
    ("string" | "number" | "integer" | "boolean"). `enum` closes the domain (e.g. a <select>'s options);
    `pattern` is a full-match regex; `min`/`max` bound a number; `max_length` bounds a string. `required`
    (default) rejects a missing value; a non-required slot falls back to the step's frozen literal. A
    `secret` slot's value is resolved from the env var named by `secret_env` at replay and is NEVER passed
    in `params`, logged, or serialized (mirrors LoginSpec's env-only credential rule).

    That last promise is ENFORCED at four points, not merely asserted: the value is refused as a `params`
    key and omitted from the MCP input schema; the recorder blanks a credential field at capture so no
    plaintext reaches the cache; `flow inspect` masks the step; and `snapshot.capture(redact=...)` scrubs
    the resolved value out of every Observation before it can reach a provider. The residual, stated rather
    than hidden: the VISION tier sends a raw screenshot, where a secret typed into a plain text input is
    legible. Don't pair a secret slot with vision grounding."""

    type: str = "string"
    enum: Optional[list] = None
    pattern: Optional[str] = None
    required: bool = True
    min: Optional[float] = None
    max: Optional[float] = None
    max_length: Optional[int] = None
    secret: bool = False
    secret_env: Optional[str] = None   # env var holding a secret slot's value (required iff secret)
    description: str = ""


@dataclass
class FlowSpec:
    """A named, reusable recurring task."""

    name: str
    start_url: str
    goal: str
    extract: Optional[str] = None          # what data to pull (None = navigate-only flow)
    extract_schema: Optional[dict] = None  # optional JSON schema for the extracted `data`
    pin_read: bool = False                 # try to pin a deterministic 0-LLM read of a scalar answer
    headers: Optional[dict] = None         # auth via extra HTTP headers
    storage_state: Optional[str] = None    # auth via a Playwright storage_state JSON (cookies)
    login: Optional[Union[LoginSpec, LoginCallable]] = None  # how to (re)authenticate on expiry
    mutate: Optional[MutateSpec] = None    # set -> this is a WRITE flow (Phase D)
    slots: Optional[dict] = None           # H3: {slot_name: SlotSpec} — the typed input contract
    contracts: Optional[dict] = None       # H9: {field_path: {attr: value}} — sparse HUMAN value-contract overlay
    # H9 layer 2 (judge): None = inert (nothing captured, not one byte written) | "advisory" = capture + judge
    # + report, quarantine NOTHING | "enforce" = a corroborated finding may quarantine. Armed only by a human
    # editing the spec (`flow audit --set-mode`); there is deliberately no runtime flag that can arm it.
    audit: Optional[str] = None
    max_steps: Optional[int] = None
    headless: Optional[bool] = None

    @property
    def scope(self) -> str:
        return f"flow:{self.name}"


@dataclass
class LearnResult:
    spec: FlowSpec
    cached: bool       # did a replayable flow get cached?
    steps: list        # the learned steps, for the developer to inspect
    data: Any = None   # extracted data
    found: bool = False
    approved: bool = False
    shape: Any = None  # signature of the extracted data's structure (for replay drift checks)
    pinned: bool = False  # did a deterministic 0-LLM read get pinned (pin_read flows)?
    performed_write: bool = False  # did discovery actuate a mutating step? (best-of-N must not retry)
    note: str = ""


@dataclass
class FlowMeta:
    """Trust + run-history metadata for a learned flow (sidecar next to the cached flow)."""

    approved: bool = False
    shape: Any = None
    learned_ts: float = 0.0
    last_ok_ts: float = 0.0
    # run history (for the fleet health view)
    last_run_ts: float = 0.0
    last_error: Optional[str] = None
    last_error_ts: float = 0.0
    runs: int = 0
    successes: int = 0
    consecutive_failures: int = 0
    read_pin: Optional[dict] = None  # a pinned 0-LLM read (locator + value type), if learned
    # H3 slice 2a: a hash of the slot schema (`FlowSpec.slots`) that was APPROVED. Replay of a slotted
    # flow refuses if the current schema no longer matches — a domain widened after approval (e.g. a
    # payee enum loosened to any string) is a stale-approval injection surface, worst on a write.
    slots_hash: Optional[str] = None
    # H9 layer 1: deterministic per-field VALUE contracts (fail loud on a same-shape-but-wrong value).
    # `contracts` = the MACHINE seed auto-derived at learn (learn-bound like `shape`, NOT hashed);
    # `contracts_hash` = the approved hash of the HUMAN overlay `FlowSpec.contracts` (tighten OR loosen
    # re-approves); `quarantine` = a persisted violation record `{code, reason, ts}` (None = clean) that
    # makes every future run refuse 0-LLM until `release()`. Named `quarantine` (NOT `quarantined`) so the
    # forward-compat test whose fake future key is literally `"quarantined"` still asserts drop-unknown.
    contracts: Optional[dict] = None
    contracts_hash: Optional[str] = None
    quarantine: Optional[dict] = None
    # The RECIPE a human actually approved: `cache.steps_hash(cached_flow)` stamped at `approve()`. The
    # other two hashes above bind the SPEC (slot schema, contract overlay); this one binds the STEPS, which
    # is what "approved" means to a human — they read `flow inspect` and said yes to those actions on those
    # targets. Without it, `approved` was a sticky bit that survived the flow being re-authored underneath
    # it (a heal that retargets a locator, a suffix-replan, a re-record, a re-learn, a hand-edited cache
    # file), so an approved WRITE could fire steps no human ever saw. `None` on a flow approved by an older
    # version -> replay REFUSES with a migration message rather than back-filling (a lazy back-fill would
    # rubber-stamp exactly the drifted recipe the gate exists to catch). See `cache.steps_hash`.
    steps_hash: Optional[str] = None
    # H9 layer 2 (judge): `audit_due` marks the NEXT replay as must-capture (set after a heal/replan, a
    # re-learn, or a human `release()` — the highest-risk runs); `audit_advisories` counts unreviewed
    # advisory findings so habituation is MEASURED (surfaced by `flow status`), never a silent pile-up.
    audit_due: bool = False
    audit_advisories: int = 0


@dataclass
class FlowHealth:
    """A flow's status for the fleet view."""

    name: str
    status: str  # not-learned | never-run | healthy | failing | stale | quarantined | unreadable
    cached: bool
    approved: bool
    runs: int
    successes: int
    consecutive_failures: int
    last_run_ts: float
    last_ok_ts: float
    last_error: Optional[str]
    # True when `approved` is set but no longer binds the steps on disk (they were re-authored, or the flow
    # was approved before the binding existed). Such a flow REFUSES at pre-flight with `stale_approval`, so
    # the fleet view surfaces it rather than letting an operator find out at run time. Defaulted so callers
    # that construct a FlowHealth positionally (tests, fixtures) keep working.
    approval_stale: bool = False


class FlowReplayError(RuntimeError):
    """Replay could not be trusted: no cached flow, page drift, data not found, or shape change.

    Base of a small TYPED taxonomy so a caller (esp. the H2 MCP server) can react to a failure by
    KIND without string-parsing the message: `.code` is a stable machine-readable slug and
    `.retryable` says whether re-running as-is could plausibly succeed. Every subclass still IS a
    `FlowReplayError`, so existing `except FlowReplayError` keeps catching all of them (the change is
    additive — the base is still raised for config refusals like not-approved / no-confirm-check)."""

    code = "replay_error"
    retryable = False
    # Did the WRITE already land when this was raised? Only ever True where the code POSITIVELY knows it
    # did — it arms the retry-dedupe ledger, and `ledger.py`'s invariant is "never a false skip of an
    # un-landed write", so a maybe is a no. Callers that own a ledger (run_batch, the MCP write surface)
    # record the row on the way past instead of leaving the one case they KNOW committed unrecorded.
    landed = False


class DriftError(FlowReplayError):
    """The page or a locator drifted — the learned path no longer matches. Do NOT retry as-is;
    re-learn (or use `on_drift='relearn'`) after a human checks what changed."""

    code = "drift"
    retryable = False


class ShapeDriftError(FlowReplayError):
    """The extracted data's STRUCTURE changed vs the learned run (a field vanished, a scalar became a
    list). Do NOT retry — returning it would be silently-wrong data; the flow needs review."""

    code = "shape_drift"
    retryable = False


class AuthExpiredError(FlowReplayError):
    """The login session expired. Safe to retry AFTER refreshing auth (`flow login` / a `login` spec).
    (Raised only when expiry is unambiguous; the heuristic auth-refresh path inside `replay` can't
    confidently attribute a generic drift to expiry, so that path raises `DriftError`.)"""

    code = "auth_expired"
    retryable = True


class EscalateError(FlowReplayError):
    """An interstitial / CAPTCHA / human-verification wall blocks replay. A machine cannot proceed —
    escalate to a human. Not retryable by the agent."""

    code = "escalate"
    retryable = False


class ParamValidationError(FlowReplayError):
    """A supplied param violated the slot contract (out-of-domain / unknown name / missing-required / a
    secret passed in params). CALLER-FIXABLE: fix the arguments, do not retry as-is. Raised pre-browser by
    `validate_params`, so it's never confused with a replay-time drift/auth failure or an operator-config gap
    (a secret's env being UNSET, or a stale approval, keep the base `replay_error` — those aren't the
    caller's arguments to fix)."""

    code = "invalid_params"
    retryable = False


class FlowQuarantineError(FlowReplayError):
    """A replayed value violated its learned VALUE CONTRACT (H9): a same-shape-but-WRONG value — a field that
    went null, a number that flipped non-positive, a list that collapsed below its count floor. The flow is
    QUARANTINED (persisted): every future run refuses 0-LLM at pre-flight until a human investigates the value
    and `flow release`s it. NOT retryable — re-running can't fix wrong data, and a relearn would bypass the
    quarantine. Value-free by construction (the message names types/counts/bounds, never an extracted value)."""

    code = "quarantined"
    retryable = False


class StaleApprovalError(FlowReplayError):
    """The flow's cached RECIPE no longer matches what a human approved (`cache.steps_hash` != the digest
    stamped by `approve()`), or it was approved by a version that predates the binding.

    Raised pre-browser at pre-flight, so nothing has acted yet. NOT retryable and NOT healable: only a human
    re-reading the steps (`flow inspect`) and re-approving (`flow approve`) can clear it — automating that
    away would restore exactly the sticky-approval hole this closes. Deliberately absent from
    `_classify_replay_failure`: this is a config/trust refusal decided BEFORE any attempt, never a
    classification of an attempt's failure `kind`."""

    code = "stale_approval"
    retryable = False


class UnkeyedWriteError(FlowReplayError):
    """A flow DECLARED as a write plans zero Idempotency-Keys, so the retry-dedupe floor is absent.

    Raised pre-browser at pre-flight, before anything actuates. NOT retryable — retryability is exactly
    what is missing. A human must re-record the flow so its commit is captured as a real write, or move it
    to a human-gated surface if the commit is a bare GET link that cannot be made retry-safe."""

    code = "unkeyed_write"
    retryable = False


class WriteUnverifiedError(FlowReplayError):
    """The commit ACTUATED, but the whole-flow confirm was already true before it ran — so nothing on the
    page can distinguish a landed write from a signal that was already there.

    NOT retryable: the write may have committed, so re-running risks a double-submit. NOT `landed` either:
    it may equally not have, and recording it would be a false skip — `ledger.py`'s invariant is "never a
    false skip of an un-landed write", and a keyed retry is the safer side of that trade. The human action
    is to check the target system, then re-record with a confirm the write itself CREATES.

    (`record()` probes for this at authoring time, but only on the ENTRY page — see its refusal. In a
    multi-page flow the commit happens elsewhere, so this runtime check is the backstop that actually
    holds.)"""

    code = "write_unverified"
    retryable = False


class WriteReadbackError(FlowReplayError):
    """The write LANDED and its completion signal was confirmed, but the confirmation READBACK
    (`spec.extract` — the order number, the reference id) could not be read.

    Raised INSTEAD of returning `{"status": "confirmed", "data": None}`, which is what a caller would
    otherwise log against a real order. **Emphatically NOT retryable**: the side effect has already
    happened, so re-running would double-submit (inviolable #3). The correct human response is to read the
    reference off the page/inbox and, if the flow keeps missing it, re-record the readback — never to
    re-run. The run is recorded as a SUCCESS in fleet health for the same reason: the write succeeded, and
    a failure streak would invite exactly the retry that must not happen."""

    code = "write_readback"
    retryable = False
    landed = True     # the confirm PASSED; only the readback missed — the side effect is certain


def _classify_replay_failure(kind: str) -> type[FlowReplayError]:
    """Map an `_attempt_replay` failure `kind` to its taxonomy class (default: DriftError)."""
    return {
        "shape": ShapeDriftError,
        "escalate": EscalateError,
        "quarantine": FlowQuarantineError,
        "write_unreadable": WriteReadbackError,
        "write_unverified": WriteUnverifiedError,
        "miss": FlowReplayError,  # no learned flow — an absence, not a drift
    }.get(kind, DriftError)


# --- data-shape signature (data-level drift detection) ----------------------------------------
def _shape_of(value: Any) -> Any:
    if isinstance(value, dict):
        return {"t": "object", "keys": sorted(str(k) for k in value)}
    if isinstance(value, (list, tuple)):
        items = [_shape_of(v) for v in value]
        first = items[0] if items else None
        return {"t": "array", "item": first if all(it == first for it in items) else "mixed"}
    if isinstance(value, bool):
        return {"t": "bool"}
    if isinstance(value, (int, float)):
        return {"t": "number"}
    if value is None:
        return {"t": "null"}
    return {"t": "string"}


def _shape_matches(recorded: Any, current: Any) -> bool:
    """Lenient structural comparison — counts vary day to day, structure shouldn't."""
    if recorded is None or current is None or recorded == current:
        return True
    if isinstance(recorded, dict) and isinstance(current, dict):
        if recorded.get("t") != current.get("t"):
            return False
        if recorded.get("t") == "array":
            ri, ci = recorded.get("item"), current.get("item")
            if ri in (None, "mixed") or ci in (None, "mixed"):
                return True  # empty/mixed result -> can't disprove
            return _shape_matches(ri, ci)
        if recorded.get("t") == "object":
            return recorded.get("keys") == current.get("keys")
        return True  # same primitive type
    return False


# --- meta sidecar -----------------------------------------------------------------------------
def _meta_path(cache: FlowCache, key: str) -> Path:
    return Path(cache.root) / f"{key}.meta.json"


# Cross-process exclusive lock for the meta read-modify-write. fcntl on POSIX, msvcrt on Windows —
# both release automatically on fd close / process death, so a crashed holder never wedges others.
# Acquire NON-BLOCKING + retry: msvcrt's blocking LK_LOCK is unfair and gives up after ~10s by RAISING
# EDEADLOCK, which would silently degrade to an unlocked write under contention; a tight try-lock loop
# is fair and only ever degrades on a truly pathological (> deadline) wedge — and even then it logs
# loudly, never silently dropping a health update.
_LOCK_DEADLINE_S = 30.0
_LOCK_POLL_S = 0.01

try:  # POSIX
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False  # held by another process

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # non-blocking 1-byte region at pos 0
            return True
        except OSError:
            return False  # held by another process

    def _unlock_fd(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _meta_lock(cache: FlowCache, key: str):
    """Hold an exclusive CROSS-PROCESS lock for a flow's meta read-modify-write, so two scheduled
    processes (or an operator edit racing a scheduled run) can't lose a health/trust update
    (last-writer-wins). Locks a dedicated `<key>.meta.lock` file — never the meta file itself, which
    is atomically replaced. On a pathological wedge (no acquire within the deadline) it proceeds
    UNLOCKED but **logs loudly** — it never silently drops an update. NOTE: acquisition is a synchronous
    spin on the caller's event-loop thread, so the guarded critical section must stay tiny."""
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    lock_path = Path(cache.root) / f"{key}.meta.lock"
    f = None
    locked = False
    try:
        try:
            f = open(lock_path, "a+")
            f.seek(0)
        except OSError as exc:  # can't even open the lock file -> degrade, but loudly
            _log.warning("meta lock: cannot open %s (%s) — proceeding unlocked", lock_path, exc)
            yield
            return
        deadline = time.monotonic() + _LOCK_DEADLINE_S
        while not (locked := _try_lock(f.fileno())):
            if time.monotonic() >= deadline:
                _log.warning("meta lock for %s not acquired in %.0fs — proceeding UNLOCKED (possible "
                             "lost health update under extreme contention)", key, _LOCK_DEADLINE_S)
                break
            time.sleep(_LOCK_POLL_S)
        yield
    finally:
        if f is not None:
            if locked:
                try:
                    f.seek(0)
                    _unlock_fd(f.fileno())
                except OSError:
                    pass
            f.close()


def _update_meta(cache: FlowCache, key: str, mutate: Callable[["FlowMeta"], None]) -> None:
    """Load → mutate → atomically save a flow's meta UNDER the cross-process lock. Every writer of the
    meta sidecar (run records, learn, approve/unapprove, relearn pin-clear) goes through this, so a
    scheduled run record can't be clobbered by a concurrent operator edit of the same flow (or vice
    versa). Reads (health views, the replay snapshot) need no lock — the atomic save never tears."""
    with _meta_lock(cache, key):
        meta = _load_meta(cache, key)
        mutate(meta)
        _save_meta(cache, key, meta)


_META_UNREADABLE = (
    "the trust sidecar is UNREADABLE (corrupt or torn) — approval, quarantine, contracts, the recipe "
    "digest and the 0-LLM read pin could not be recovered. Inspect the preserved `.corrupt.*` copy, then "
    "re-learn and re-approve."
)


def _poisoned_meta() -> FlowMeta:
    """The meta to hand back when the sidecar can't be read: a QUARANTINE, not a blank slate."""
    return FlowMeta(quarantine={"code": "meta_unreadable", "reason": _META_UNREADABLE, "ts": time.time()})


def _preserve_corrupt(p: Path) -> None:
    """Move an unreadable sidecar aside before anything can overwrite it. The meta is the HOT file (every
    replay rewrites it via `_record_run`), so without this the next run destroys the only evidence."""
    try:
        os.replace(p, p.with_name(f"{p.name}.corrupt.{int(time.time())}"))
    except OSError as exc:  # noqa: BLE001 — best effort; the refusal below is the load-bearing part
        _log.warning("could not preserve the corrupt flow meta %s: %s", p, exc)


def _refuse_unreadable_meta(cache: FlowCache, key: str, p: Path, why: str) -> FlowMeta:
    """Preserve the corrupt sidecar, PERSIST a quarantine in its place, and return that quarantine.

    Persisting is the load-bearing half. Moving the bad file aside and returning an in-memory quarantine
    would only postpone the wipe by one call: the very next `_load_meta` would find the path ABSENT and
    hand back a virgin `FlowMeta()` — the same silent trust reset, one step later. Writing the refusal makes
    it survive, and makes it idempotent (the second load reads valid JSON, so nothing is preserved twice).
    """
    _log.error("flow meta %s is unreadable (%s) — refusing to run on a default trust state", p, why)
    _preserve_corrupt(p)
    meta = _poisoned_meta()
    try:
        _save_meta(cache, key, meta)
    except OSError as exc:  # noqa: BLE001 — read-only/full disk: the caller still gets the refusal in hand
        _log.error("could not persist the meta-unreadable quarantine for %s (%s) — this run refuses, but "
                   "the NEXT one would see an absent sidecar and start from a clean trust state", p, exc)
    return meta


def _load_meta(cache: FlowCache, key: str) -> FlowMeta:
    """Read a flow's trust sidecar. ABSENT and UNREADABLE are deliberately NOT the same thing.

    Absent -> a virgin `FlowMeta()` (a flow that was never learned has no trust state to lose). Unreadable
    -> a meta carrying a `meta_unreadable` QUARANTINE, so every surface refuses at pre-flight instead of
    running on a default trust state. Returning `FlowMeta()` for a torn file — as this used to — silently
    dropped quarantine, approval, shape, contracts, the steps digest and `read_pin` in one step: a flow
    quarantined for returning a WRONG value would return that value again as a clean success, and the wiped
    pin would put the LLM extractor back on a replay that was pinned 0-LLM (inviolable #1). It never
    raises: `health()` and the MCP tools/list loop must not traceback on one bad flow.

    The sibling readers already got this right — `cache.get` returns None on a corrupt flow (which surfaces
    as `mode == "miss"`, fail-loud) and `load_history` now preserves + reports its own corruption.
    """
    p = _meta_path(cache, key)
    if p.exists():
        # A TRANSIENT read failure is not corruption, and the two must not share a branch. On Windows the
        # classic case is an AV/indexer sharing violation (WinError 32) moments after `os.replace`; a
        # network or removable store gives the same shape. Treating that as permanent DESTROYED a perfectly
        # healthy sidecar on the first occurrence — renamed aside and replaced with a quarantine — and the
        # documented recovery (`flow release`, then `flow approve`) then brought the flow back with
        # `contracts=None`, `shape=None` and `read_pin=None`: its H9 value gate and shape gate silently
        # gone, and the LLM extractor back on a replay that was pinned 0-LLM. Retrying costs milliseconds;
        # being wrong costs the trust state the file exists to hold.
        raw, io_error = None, None
        for attempt in range(3):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                io_error = None
                break
            except (ValueError, UnicodeDecodeError) as exc:
                # PARSE failure — the bytes are there and they are not JSON. That is real corruption; a
                # retry would read the same bytes. Fail over to the quarantine immediately.
                return _refuse_unreadable_meta(cache, key, p, f"{type(exc).__name__}: {exc}")
            except OSError as exc:
                io_error = exc                      # could be transient: back off briefly and re-read
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
        if io_error is not None:
            # Still unreadable after retries. Do NOT destroy it — we never saw its bytes, so we cannot
            # claim it is corrupt. Refuse this run loudly on a poisoned in-memory meta and leave the file
            # exactly as it is for the next run (or a human) to read.
            _log.error("flow meta %s could not be READ after 3 attempts (%s) — refusing this run on a "
                       "quarantine, and leaving the file untouched (it may be perfectly healthy)", p,
                       io_error)
            return _poisoned_meta()
        if not isinstance(raw, dict):
            # Valid JSON, wrong document (a bare list/scalar from a truncated-then-patched file or a bad
            # hand-edit). Same treatment: it is not a meta, so it must not read as "no meta".
            return _refuse_unreadable_meta(cache, key, p, f"not an object ({type(raw).__name__})")
        # Forward-compat: a meta written by a NEWER version may carry fields this version doesn't
        # know. Drop only the unknown keys and keep the rest — NEVER let one unexpected key make
        # `FlowMeta(**raw)` raise and reset approval + run history to defaults (a silent trust wipe).
        unknown = [k for k in raw if k not in {f.name for f in dataclasses.fields(FlowMeta)}]
        if unknown:
            sig = frozenset(unknown)
            if sig not in _warned_unknown_meta_keys:  # warn once per distinct key-set, not per load
                _warned_unknown_meta_keys.add(sig)
                _log.warning(
                    "flow meta carries field(s) %s this version doesn't know — ignoring them and "
                    "preserving approval + run history (metas with these keys won't be re-logged)",
                    sorted(unknown),
                )
        return FlowMeta(**_only_known(raw, FlowMeta))
    return FlowMeta()


def _save_meta(cache: FlowCache, key: str, meta: FlowMeta) -> None:
    """Atomically + DURABLY persist a flow's trust sidecar.

    temp + `os.replace` alone never lets a reader see a torn file, but it does not survive a host crash or
    power loss: `os.replace` can land while the temp file's BYTES are still only in the page cache, leaving
    a zero-length or NUL-filled sidecar on the next boot. The meta is the hot file (every replay rewrites
    it), so that window is hit often enough to matter — and a torn meta is exactly the input `_load_meta`
    now has to quarantine on. `fsync` before the rename closes it, mirroring `ledger.py`, which has always
    done this."""
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    p = _meta_path(cache, key)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(meta), indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def _record_run(cache: FlowCache, key: str, *, ok: bool, error: Optional[str] = None) -> None:
    """Record a replay outcome into the flow's run history (for the fleet health view). The
    read-modify-write runs under `_meta_lock` (via `_update_meta`), so concurrent records OR a
    concurrent operator edit of the same flow can't lose a run-count / failure-streak update.
    """
    def _apply(meta: FlowMeta) -> None:
        now = time.time()
        meta.last_run_ts = now
        meta.runs += 1
        if ok:
            meta.last_ok_ts = now
            meta.successes += 1
            meta.consecutive_failures = 0
            meta.last_error = None
        else:
            meta.consecutive_failures += 1
            meta.last_error = error
            meta.last_error_ts = now

    _update_meta(cache, key, _apply)


def _default_cache() -> FlowCache:
    return FlowCache()


def _router(provider_name: str):
    provider = get_provider(provider_name)
    return provider, getattr(provider, "router", None) or build_router(provider_name)


# Env vars whose presence means the configured provider's LLM is usable — so `record` can auto-caption step
# intents. Absent (the key-less CI / test path) -> caption is skipped, never an LLM attempt (the router
# retries with backoff, so a failed attempt per test would be slow + noisy).
_KEY_ENV = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def _llm_configured(provider_name: str) -> bool:
    return any(os.getenv(e) for e in _KEY_ENV.get(provider_name, ("ANTHROPIC_API_KEY",)))


def caption_for(provider_name: Optional[str] = None):
    """Build the intent captioner to pass as `record(caption=...)` — or None when no LLM is configured (so
    recording stays key-less). Used by the `flow record` CLI; the captioner is best-effort, so a failure
    just leaves placeholder intents. NOT called by `record()` itself: caption is opt-in, never a surprise
    LLM call on the key-less capture path."""
    pname = provider_name or settings.provider
    if not _llm_configured(pname):
        return None
    _, router = _router(pname)
    return lambda g, s: caption_intents(router, g, s)  # noqa: E731


def _redacted_body_text(session, redact: tuple) -> "Any":
    """The page's body text with the run's resolved secrets scrubbed, for the LLM extractor.

    `snapshot.capture(redact=...)` was introduced as "the one place" every snapshot -> LLM path runs
    through. The EXTRACTOR is a page-text -> LLM path that does not run through it: it reads
    `page.inner_text("body")` directly and hands up to 12000 chars to the strong tier. And unlike the heal
    and suffix-replan paths, it needs no drift at all — every replay of a read flow without a resolved
    `read_pin`, and every write flow with a readback, makes this call.

    The guard already existed 500 lines away in this same file: `_capture_audit` scrubs the IDENTICAL
    page-text channel with the SAME `_secret_values(spec)` before it hits DISK. It simply was never applied
    before the text hits the MODEL."""
    async def _read() -> str:
        try:
            text = await session.page.inner_text("body")
        except Exception:  # noqa: BLE001
            return ""
        for term in redact:
            if term:
                text = text.replace(term, REDACTED)
        return text
    return _read()


def _make_pre_write(spec: FlowSpec, out: dict):
    """The whole-flow confirm's BASELINE probe, or None when there is no whole-flow confirm to baseline.

    Handed to `run_cached(pre_write=...)`, which calls it once, immediately before the run's FIRST write
    actuates. `_make_finalize` then requires an absent->present TRANSITION rather than mere presence."""
    if spec.mutate is None or not spec.mutate.has_confirm():
        return None
    m = spec.mutate

    async def _pre(session) -> None:
        out["_pre_confirm"] = await condition_present(
            session.page, selector=m.confirm_selector, text_contains=m.confirm_text_contains,
            url_contains=m.confirm_url_contains, timeout_ms=0,   # a single immediate check
        )

    return _pre


def _make_finalize(spec: FlowSpec, router, out: dict, pin: Optional[dict] = None,
                   redact: tuple = ()):
    async def _finalize(session):
        if spec.mutate is not None:
            # WRITE flow: success is action-completion — the declared confirm check must hold,
            # else the write didn't land and replay fails loud (Phase D).
            m = spec.mutate
            try:
                await session.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            # TRANSITION, not presence (A8). The per-write barrier has always required this; the
            # whole-flow one — the ONLY barrier a single-write flow has, since `step_confirms` is optional
            # — did a bare post-hoc check. So a JS-only regression that stops the POST read as "confirmed":
            # the DOM is unchanged so the mutation gate passes, and a persistent banner from a PREVIOUS
            # order satisfies the check. Under `run_batch(resume=...)` the un-landed row was then written to
            # the ledger as committed and permanently skipped — against `ledger.py`'s stated invariant,
            # "never a false skip of an un-landed write".
            pre = bool(out.get("_pre_confirm"))
            confirmed = (not pre) and await condition_present(
                session.page, selector=m.confirm_selector,
                text_contains=m.confirm_text_contains, url_contains=m.confirm_url_contains,
                timeout_ms=m.timeout_ms,
            )
            data = None
            if spec.extract is not None:  # optionally also pull a confirmation number, etc.
                text = await _redacted_body_text(session, redact)
                ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
                data = ex.data
                # Keep the READBACK's own outcome on a SEPARATE channel from `found`. `out["found"]` must
                # stay bound to `confirmed` (the caller turns a false `found` into "write not confirmed",
                # which invites a retry — and this write LANDED). But dropping ex.found/error/truncated
                # entirely, as this branch used to, meant a failed readback returned
                # {"status": "confirmed", "data": None} — a caller logging that records a null against a
                # real order. The sibling READ branch below has always propagated these; the write branch
                # is the path the GUIDE mandates for writes and it did not.
                out["extract_found"] = ex.found
                out["extract_error"] = ex.error
                out["truncated"] = ex.truncated
            out["data"], out["found"] = data, confirmed
            out["confirm_pre_true"] = pre
            out["error"] = None if confirmed else (
                "the commit ACTUATED and its write may well have landed, but the whole-flow confirm was "
                "ALREADY TRUE before it ran, so nothing here can tell a landed write from a signal that "
                "was already on the page. DO NOT simply re-run this: check the target system for the "
                "effect first. Then pick a confirm the write itself CREATES (an order number, a URL the "
                "commit navigates to), not a persistent status region — and re-record." if pre else
                "write not confirmed (no completion signal on the page)")
            return {"solved": confirmed, "data": data}
        if spec.extract is None:
            out["found"] = True  # navigate-only flow: reaching the end IS success
            return {"solved": True}
        try:
            await session.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        if pin is not None:  # REPLAY of a pinned flow: read the answer deterministically (0 LLM)
            val = await read_pin(session.page, pin)
            if val is not None:
                out["data"], out["found"], out["pinned"] = val, True, True
                return {"solved": True, "data": val}
            out["found"] = False
            out["error"] = "pinned read could not resolve or cleanly parse (page changed) — re-learn the flow"
            return {"solved": False}
        text = await _redacted_body_text(session, redact)
        ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
        if ex.truncated and not ex.found:
            # The page was longer than the extractor's window AND the value wasn't in the visible portion:
            # a "not found" here is INDETERMINATE (the answer may be past the cut), not a trustworthy
            # absence a scheduler should treat as real. Fail loud instead of silently reporting a miss.
            out["data"], out["found"], out["truncated"] = None, False, True
            out["error"] = ("page too large to read fully — the extractor input was truncated and the "
                            "value was not in the visible portion; narrow the page or pin the read")
            return {"solved": False}
        out["data"], out["found"], out["error"] = ex.data, ex.found, ex.error
        if ex.truncated:
            # Found, but on a truncated page, so a list may be short. extract() already LOGGED a warning
            # (the actual signal today); this records a breadcrumb, but NOTE: no caller consumes
            # out["truncated"] yet, so replay()'s return can't distinguish a short list from a complete
            # one. List-completeness (fail-loud on a count drop) is deferred to the H9 value-contracts
            # feature — this branch is a warning + a marker, NOT a completeness guarantee.
            out["truncated"] = True
        if spec.pin_read and ex.found:  # LEARN: try to pin a 0-LLM read of the answer for replays
            out["pin"] = await find_pin(session.page, ex.data)
        return {"solved": ex.found, "data": ex.data}

    return _finalize


# --- auth refresh (re-login when a cookie session expires) ------------------------------------
def _same_page(a: str, b: str) -> bool:
    from urllib.parse import urlsplit

    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.netloc, pa.path.rstrip("/")) == (pb.netloc, pb.path.rstrip("/"))


async def _form_login(page, login: LoginSpec) -> None:
    user = os.environ.get(login.username_env)
    pw = os.environ.get(login.password_env)
    if not user or not pw:
        raise FlowReplayError(
            f"login credentials not in env (need {login.username_env} and {login.password_env})"
        )
    to = {"timeout": login.timeout_ms} if login.timeout_ms else {}  # per-step ceiling, if set
    await page.goto(login.url, wait_until="domcontentloaded", **to)
    try:
        user_loc = (
            page.locator(login.username_selector) if login.username_selector
            else page.locator("input[type=email], input[type=text], input[type=tel]")
        ).first
        await user_loc.fill(user, **to)
        pass_loc = page.locator(login.password_selector or "input[type=password]").first
        await pass_loc.fill(pw, **to)
        if login.submit_selector:
            await page.locator(login.submit_selector).first.click(**to)
        else:
            await pass_loc.press("Enter", **to)
    except Exception as exc:  # noqa: BLE001 - heuristic selectors may not match; guide the user
        raise FlowReplayError(
            f"could not auto-fill the login form at {login.url} ({type(exc).__name__}) — pass "
            f"explicit username_selector/password_selector/submit_selector, or a callable login "
            f"for multi-step/SSO flows"
        ) from None
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:  # noqa: BLE001
        pass


async def _login_succeeded(page, login: LoginSpec) -> bool:
    if login.success_selector:
        try:
            await page.wait_for_selector(login.success_selector, timeout=login.timeout_ms or 5000)
            return True
        except Exception:  # noqa: BLE001
            return False
    if login.success_url_contains:
        return login.success_url_contains in page.url
    return not _same_page(page.url, login.url)  # default: assume success if we left the login page


async def _already_committed(spec: FlowSpec) -> bool:
    """Idempotency precheck: open a fresh page at `mutate.precheck_url` (default the start_url) and
    report whether the desired end-state is ALREADY present — so a one-shot write isn't re-fired.

    A separate, read-only pre-pass (not a `prepare` hook): it must not run the cached steps, and it
    sidesteps the per-step mutation gate entirely. Reads the live page each call, so a legitimately
    recurring write (whose end-state isn't present on a fresh visit) is never wrongly skipped.
    """
    m = spec.mutate
    session = await BrowserSession(
        headless=spec.headless, storage_state=spec.storage_state
    ).start()
    try:
        if spec.headers:
            await session.set_extra_http_headers(spec.headers)
        await session.goto(m.precheck_url or spec.start_url)
        return await condition_present(
            session.page, selector=m.precheck_selector, text_contains=m.precheck_text_contains,
            url_contains=m.precheck_url_contains, timeout_ms=0,  # a fast skip decision, not a wait
        )
    finally:
        await session.close()


async def _precheck_done(spec: FlowSpec) -> bool:
    """True if this is a write flow with an idempotency precheck whose end-state already holds."""
    return spec.mutate is not None and spec.mutate.has_precheck() and await _already_committed(spec)


async def refresh_auth(spec: FlowSpec, *, headless: Optional[bool] = None) -> None:
    """Re-authenticate `spec.login` and save fresh cookies to `spec.storage_state`.

    Credentials come from the env vars named in the LoginSpec (or are handled by a callable
    login); they are never logged or written into the cached flow — only the resulting cookies.
    For a `LoginSpec`, the login is verified before saving (and the save is atomic), so a failed
    login can't overwrite a working session's cookies.
    """
    if spec.login is None:
        raise FlowReplayError(f"{spec.name!r}: no `login` configured — cannot refresh auth")
    if not spec.storage_state:
        raise FlowReplayError(f"{spec.name!r}: set `storage_state` (a path) so refreshed cookies can be saved")
    _log.info("flow %r: refreshing auth (re-login -> %s)", spec.name, spec.storage_state)
    session = await BrowserSession(
        headless=headless if headless is not None else spec.headless
    ).start()  # a fresh context (no stale cookies) for a clean login
    try:
        if callable(spec.login):
            await spec.login(session.page)
        else:
            await _form_login(session.page, spec.login)
            if not await _login_succeeded(session.page, spec.login):
                raise FlowReplayError(
                    f"{spec.name!r}: login did not appear to succeed (still on the login page or "
                    f"success check unmet) — check credentials/selectors; storage_state left unchanged"
                )
        # Atomic save: write a temp file then replace, so a crash mid-write can't corrupt the
        # working storage_state (and we only get here once login is verified).
        Path(spec.storage_state).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{spec.storage_state}.tmp"
        await session.save_storage_state(tmp)
        os.replace(tmp, spec.storage_state)
        _log.info("flow %r: auth refreshed OK", spec.name)
    finally:
        await session.close()


# --- learn / approve / replay -----------------------------------------------------------------
async def learn(
    spec: FlowSpec, *, samples: int = 1, provider_name: Optional[str] = None, provider=None,
    router=None, cache: Optional[FlowCache] = None, verify_replay: bool = True,
) -> LearnResult:
    """LLM-author the flow, cache it, record its output shape, and return it to inspect.

    Discovery (the learn run) is the reliability bottleneck — the LLM sometimes fails to author a
    working flow. `samples > 1` re-authors up to N times and keeps the FIRST attempt the verifier
    confirms (`found` — data extracted / write confirmed / navigate-only solved), trading LLM cost
    for a higher first-try success rate on flaky/ambiguous pages. Each attempt gets a fresh
    provider+router (so the LLM resamples); passing an explicit `provider` AND `router` forces a
    single attempt (a stateful teacher can't be replayed). A re-learn preserves any `approved` flag.
    """
    cache = cache or _default_cache()
    fixed = provider is not None and router is not None  # a caller-supplied teacher -> one attempt
    # NEVER multi-sample a declared write flow: each attempt re-performs the write (double-submit).
    if spec.mutate is not None:
        samples = 1
    attempts = 1 if fixed else max(1, samples)
    best: Optional[LearnResult] = None
    for i in range(attempts):
        if fixed:
            p, r = provider, router
        else:
            dp, dr = _router(provider_name or settings.provider)  # fresh each attempt -> LLM resamples
            p = provider if provider is not None else dp
            r = router if router is not None else dr
        res = await _learn_once(spec, provider=p, router=r, cache=cache, verify_replay=verify_replay)
        if res.cached and res.found:
            if i:
                _log.info("flow %r: discovery verified on attempt %d/%d", spec.name, i + 1, attempts)
            return res  # the cache now holds this verified flow
        if res.performed_write:  # an undeclared write was actuated -> stop, never re-author it
            _log.warning("flow %r: a write was performed during discovery — not re-sampling", spec.name)
            return res
        best = res
    if attempts > 1:
        _log.warning("flow %r: discovery unverified after %d samples", spec.name, attempts)
    return best


async def _learn_once(
    spec: FlowSpec, *, provider, router, cache: FlowCache, verify_replay: bool = True,
) -> LearnResult:
    """One discovery attempt: LLM-author the flow, cache it, record its output shape.

    `verify_replay=True` (default): only cache the authored flow if it reproduces on a 0-LLM replay
    from a fresh session, so a flow that looked solved in-session but won't replay is never cached
    (it surfaces as `cached=False`). Write flows are exempt inside the engine (no double-submit).
    """
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=provider, cache=cache, mode="learn",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out, redact=_secret_values(spec)),
        pre_write=_make_pre_write(spec, out),
        verify_replay=verify_replay, redact=_secret_values(spec),
    )
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    # ONLY the flow THIS attempt authored counts. `cache.get(key)` also returns a PRE-EXISTING flow, which
    # would report a FAILED re-learn as `cached=True` off someone else's recipe: best-of-N would stop
    # resampling, the "nothing was cached" warning would never print, and — worst — the meta refresh below
    # would re-bind `read_pin` (and audit_due) onto steps this run never authored, so an approved flow would
    # replay its OLD steps and read the REJECTED attempt's pin locator on the old page. That is a silently
    # WRONG value, not just authoring hygiene: the approval gate can't see it, because the steps didn't
    # change. The engine already computed the right answer (`flow._learn` sets extra["cached"]) and its own
    # best-of-N loop uses it — this is the sibling path that didn't. `.get()` because the early returns
    # (miss / escalate / no-provider) carry no "cached" key, and falsy is correct for all of them.
    cached = cache.get(key) if report.extra.get("cached") else None
    # An unattributable write is now refused by `flow._learn` ITSELF, which never caches it — so this
    # surface only has to REPORT it. That move matters: `ultracua run` and the daemon call `run_cached`
    # directly and never reach this function, so a guard living only here protected one of three callers.
    if report.extra.get("write_unattributed"):
        # DELETE any flow already on disk under this key. `_learn` refused, so it cached nothing THIS
        # run — but a previous learn may have left a recipe there, and that recipe is exactly the kind
        # this refusal exists to distrust (an older pass that mis-gated the same write). The guard this
        # replaced did `cache.delete(key)`; dropping it would silently keep serving the stale flow to
        # replay while reporting cached=False.
        stale = cache.get(key)
        if stale is not None:
            cache.delete(key)
            _log.warning("learn %r: refused an unattributable write and DELETED the previously cached "
                         "flow under the same key — it may carry the same mis-placed gate", spec.name)
        return LearnResult(
            spec=spec, cached=False, steps=list(stale.steps) if stale else [], data=out.get("data"),
            found=False, performed_write=True,
            note="a write fired on the wire during discovery but no step could be attributed to it — "
                 "refusing to cache it, because the write would replay with no mutation gate, no "
                 "precondition and no Idempotency-Key, or with those attached to a step that never "
                 "writes. `learn()` can only attribute a write that fires from its own action; a "
                 "DEFERRED one (a debounce, a timer, an awaited round-trip) cannot be tied to a cause "
                 "and is refused here — and `record()` refuses it too, for the same reason. Such a flow "
                 "is not authorable today; see docs/open-defects.md R3.2.")
    # BELT AND BRACES, and possibly now UNREACHABLE — kept deliberately, and labelled honestly rather
    # than defended. This used to be the ONLY guard, and as the sole guard it was wrong twice over: it
    # asks `not any(s.mutating)`, an INFERENCE that any sibling step whose intent text trips the keyword
    # classifier makes false (measured: an intent of "submit the search" disarmed it and cached a flow
    # whose real commit was marked a read), and it sat on a surface two of three callers never reach.
    # `_learn` now refuses in the mechanism whenever a wire write cannot be reconciled with the recipe,
    # which should subsume every case this branch caught. "Should" is doing real work in that sentence:
    # it is a claim about a control-flow argument, not something measured, so the branch stays. If a
    # later change proves it dead, DELETE it — do not leave a guard nobody can reach implying cover it
    # does not give.
    if cached is not None and report.extra.get("performed_write") and not any(
            s.mutating for s in cached.steps):
        cache.delete(key)
        return LearnResult(
            spec=spec, cached=False, steps=list(cached.steps), data=out.get("data"), found=False,
            performed_write=True,
            note="a write fired on the wire during discovery but no step could be attributed to it — "
                 "refusing to cache a write flow with zero gated steps (it would replay with no mutation "
                 "gate, no precondition and no Idempotency-Key). Author it with `flow record`, which "
                 "attributes each write that fires directly from its own action.")
    # Phase G: attach per-write completion barriers (in commit order) to the LLM-authored mutating steps.
    # A mismatch refuses the flow (delete + cached=False) — never a half/mis-confirmed multi-write flow.
    if cached is not None and spec.mutate is not None and spec.mutate.step_confirms:
        if spec.mutate.is_multiwrite():
            # The LLM-learn path classifies writes by `classify_mutation` alone, which can MISS a formless
            # write (so the 1:1 count check would falsely pass with an unbarriered write). The recorder has
            # per-write wire attribution, so a MULTI-write barrier must be authored via `record()`. (A single
            # per-write barrier is fine — a missed lone write fails the count check.)
            cache.delete(key)
            return LearnResult(spec=spec, cached=False, steps=list(cached.steps), data=out.get("data"),
                               found=False, note="a multi-write flow's per-write barriers must be recorded "
                                                 "via `flow record` (the LLM-learn path can't reliably attribute "
                                                 "each write); learn the reads, record the writes.")
        attached, reason = _attach_step_confirms(cached, spec.mutate.step_confirms)
        if attached is None:
            cache.delete(key)
            return LearnResult(spec=spec, cached=False, steps=list(cached.steps), data=out.get("data"),
                               found=False, note=f"per-write confirm checks could not be attached: {reason}")
        cache.put(attached)
        cached = attached
    data, found = out.get("data"), bool(out.get("found"))
    pinned = False
    approved = False
    if cached is not None:
        was_approved = _load_meta(cache, key).approved

        def _apply(meta: FlowMeta) -> None:  # under the lock: preserve `approved`, refresh shape/pin
            nonlocal pinned, approved
            meta.learned_ts = time.time()
            # Bind the pin to the just-learned DOM: a re-learn ALWAYS resets it (a fresh pin, or None
            # when pin_read is off / unpinnable) so a stale pin can never outlive the cached flow.
            meta.read_pin = out.get("pin") if spec.pin_read else None
            # H9 PRESERVATION: re-seeding the shape + value contracts from THIS run silently re-baselines the
            # semantic-wrongness defense. On an UN-approved flow that's right (it's still being authored). On
            # an APPROVED flow it would discard the guarantees a human blessed — and if this re-learn ran on a
            # drifted/wrong page, it would enshrine the wrong values as the new normal. So keep them: a
            # re-authored approved flow FAILS LOUD against its old contracts instead of quietly adopting new
            # ones. For a deliberate clean baseline: `flow unapprove` -> `flow learn` -> `flow approve`, or
            # `flow release --rebaseline` for a genuine permanent level shift.
            if not was_approved:
                meta.shape = _shape_of(data)
                # READ flows only — a write flow's meta.contracts stays None (the write rail is untouched).
                meta.contracts = (seed_contracts(data, truncated=bool(out.get("truncated")))
                                  if spec.mutate is None else None)
            meta.audit_due = True   # H9: a re-authored extraction is high-risk -> the next replay is audited
            pinned = meta.read_pin is not None
            approved = meta.approved

        _update_meta(cache, key, _apply)
        # H9 layer 2: a re-authored extraction restarts the magnitude baseline (learn-bound like `shape` +
        # the seed) — a fresh window re-warms rather than comparing a new-normal value to the old baseline.
        # Same reasoning as above: an APPROVED flow keeps its baseline so a re-learn can't silently reset it.
        if not was_approved:
            _reset_history(cache, key)
    else:
        approved = _load_meta(cache, key).approved
    return LearnResult(
        spec=spec, cached=cached is not None, steps=list(cached.steps) if cached else [],
        data=data, found=found, approved=approved, shape=_shape_of(data),
        pinned=pinned, performed_write=bool(report.extra.get("performed_write")),
        note=report.note or report.mode,
    )


def _slots_hash(spec: FlowSpec) -> Optional[str]:
    """A stable hash of a flow's slot schema (`FlowSpec.slots`), bound into its approval so a later
    domain change forces re-approval. None when the flow has no slots (a non-templated flow)."""
    if not spec.slots:
        return None
    canon = {name: asdict(s) if dataclasses.is_dataclass(s) else s for name, s in sorted(spec.slots.items())}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _contracts_hash(spec: FlowSpec) -> Optional[str]:
    """A stable hash of the HUMAN value-contract overlay (`FlowSpec.contracts`), bound into approval so a
    later tighten OR loosen refuses replay until re-approved (a weakened fail-loud guarantee must be
    re-blessed by a human). None when there is no overlay. The MACHINE seed (`FlowMeta.contracts`) is NOT
    hashed — it is learn-bound and re-derived only by a human-initiated re-learn."""
    if not spec.contracts:
        return None
    return hashlib.sha256(json.dumps(spec.contracts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def approve(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    """Mark a learned flow trusted (so `replay(require_approved=True)` will run it).

    Approval is BOUND to three things, and a change to any of them refuses replay until a human
    re-approves (see `_preflight_row`):
      - the RECIPE — the cached steps themselves (`cache.steps_hash`), i.e. what a human read in
        `flow inspect` and said yes to. This is the binding that makes `approved` mean something: it
        stops the system re-authoring an approved flow underneath its own approval bit.
      - the slot SCHEMA (`FlowSpec.slots`) — a widened domain is an injection surface, worst on a write.
      - the human value-contract OVERLAY (`FlowSpec.contracts`) — a weakened fail-loud guarantee.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key)
    if cached is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to approve — learn the flow first")
    sh = _slots_hash(spec)
    ch = _contracts_hash(spec)
    steps_h = steps_hash(cached)

    def _apply(m: FlowMeta) -> None:
        m.approved = True
        m.slots_hash = sh
        m.contracts_hash = ch
        m.steps_hash = steps_h

    _update_meta(cache, key, _apply)


def unapprove(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    """Withdraw approval. Also the way to RE-BASELINE an approved flow's learned data shape + value contracts:
    while approved those are deliberately preserved across a re-author, so `unapprove` -> re-author ->
    `approve` is how a human adopts a legitimately restructured page."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    # Mirror `approve`'s guard: don't silently mint a meta sidecar (and promise a re-seed) for a flow that was
    # never learned in the first place.
    if cache.get(key) is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to unapprove — learn or record the flow first")
    _update_meta(cache, key, lambda m: setattr(m, "approved", False))


def release(spec: FlowSpec, *, cache: Optional[FlowCache] = None, rebaseline: bool = False) -> None:
    """Clear a flow's H9 quarantine after a human has investigated the wrong value. Re-arms under the SAME
    contracts — if the value is still wrong the next run re-quarantines (no silent habituation; a sticky
    release means fixing the upstream value, relaxing the contract via `spec.contracts` + re-approve, or
    disabling that field). Resets the failure streak so a clean next run reports healthy.

    `rebaseline=True` (H9 layer 2): ALSO clears the rolling magnitude baseline, so a scalar-number field
    re-warms at the NEW normal instead of re-quarantining against the old one. Use ONLY for a genuine,
    permanent level shift (a price that really did move) — it does NOT inject the suspect value (which would
    leave a bimodal baseline and habituate); the field re-warms (advisory) from subsequent clean runs."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    if meta.quarantine is None:
        if rebaseline:
            _reset_history(cache, key)   # allow a pre-emptive re-baseline even when not currently quarantined
        return
    _log.info("flow %r: releasing quarantine (was: %s)%s", spec.name, meta.quarantine.get("reason"),
              " + rebaseline" if rebaseline else "")

    def _apply(m: FlowMeta) -> None:
        m.quarantine = None
        m.consecutive_failures = 0
        m.audit_due = True   # H9: the first run after a human clears a quarantine is high-risk -> audit it

    _update_meta(cache, key, _apply)
    if rebaseline:
        _reset_history(cache, key)


def _quarantine(cache: FlowCache, key: str, *, reason: str) -> None:
    """Persist an H9 value-contract quarantine (value-free reason) so every future run refuses at pre-flight
    until `release()`. Written under the meta lock via `_update_meta`, durably, before the raise."""
    _update_meta(cache, key, lambda m: setattr(
        m, "quarantine", {"code": "quarantined", "reason": reason, "ts": time.time()}))


@dataclass(frozen=True)
class QuarantineSink:
    """The audit path's ONLY effect handle. One method; one direction; a CODE, not a string.

    There is deliberately no `clear`, no `approve`, no `unapprove`, no `rebaseline`, no `meta`, and no
    free-text argument — an implementation bug cannot reach for a capability that was never handed over, and
    neither the model nor a web page can author the persisted reason (it is looked up in `audit.REASONS`,
    which raises KeyError on an unknown code rather than passing anything through). `cache`/`key` are bound at
    construction, so a sink can never be pointed at a different flow."""

    _cache: FlowCache
    _key: str
    _name: str

    def quarantine(self, code: str) -> None:
        from . import audit

        reason = f"audit({code}): {audit.REASONS[code]}"   # KeyError on an unknown code — never passthrough
        _log.warning("flow %r: QUARANTINED by audit — %s", self._name, reason)
        _quarantine(self._cache, self._key, reason=reason)


@dataclass(frozen=True)
class AdvisorySink:
    """The advisory twin: validates the code identically but writes NOTHING to the flow's trust state — it
    only bumps the unreviewed-advisory counter so habituation is measured and surfaced by `flow status`."""

    _cache: FlowCache
    _key: str
    _name: str

    def quarantine(self, code: str) -> None:
        from . import audit

        _ = audit.REASONS[code]   # same validation; an unknown code is a bug, not a silent no-op
        _log.info("flow %r: audit ADVISORY — %s", self._name, code)
        _update_meta(self._cache, self._key,
                     lambda m: setattr(m, "audit_advisories", (m.audit_advisories or 0) + 1))


def contracts_for(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> "tuple[dict, Optional[dict]]":
    """The EFFECTIVE value contracts (the machine SEED overlaid by the human `spec.contracts`) and the current
    quarantine record (or None), for `flow contracts` / inspection. Read-only."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    return effective_contracts(spec.contracts, meta.contracts), meta.quarantine


# --- H9 layer 2: rolling numeric history + the magnitude gate ---------------------------------
def _update_history(cache: FlowCache, key: str, mutate: Callable[[dict], None]) -> None:
    """Locked read-modify-write of the magnitude history sidecar (reuses `_meta_lock`; the append and any
    meta write are strictly sequential, never nested, so the non-reentrant lock is never re-acquired held)."""
    with _meta_lock(cache, key):
        doc = load_history(cache, key)
        mutate(doc)
        save_history(cache, key, doc)


def _reset_history(cache: FlowCache, key: str) -> None:
    """Drop a flow's magnitude baseline (learn/relearn re-seed, or `release(rebaseline=True)`), under the lock."""
    with _meta_lock(cache, key):
        history_path(cache, key).unlink(missing_ok=True)


def _accrue_all(doc: dict, mfields: dict) -> None:
    fields = doc.setdefault("fields", {})
    for path, value in mfields.items():
        fields[path] = accrue_ring(fields.get(path, []), value)
        # H9: the learn-time ANCHOR — the first clean observation, never overwritten. The rolling median
        # tracks a slow creep (the baseline IS the creep), so only a fixed point makes slow drift visible.
        set_anchor(doc, path, value)


def _secret_values(spec: FlowSpec) -> tuple:
    """The RUNTIME values of every secret the spec resolves from the env — the only realistic plaintext-echo
    path onto a page. Passed to the artifact redactor so a secret can never land in an audit file. The env var
    NAMES are never written either."""
    out = []
    for s in (spec.slots or {}).values():
        env = getattr(s, "secret_env", None)
        if getattr(s, "secret", False) and env:
            out.append(os.environ.get(env) or "")
    login = spec.login
    for attr in ("username_env", "password_env"):
        env = getattr(login, attr, None) if login is not None and not callable(login) else None
        if env:
            out.append(os.environ.get(env) or "")
    return tuple(v for v in out if v)


def _capture_audit(cache: FlowCache, key: str, spec: FlowSpec, meta: "FlowMeta", report, data: Any,
                   *, eff: dict, truncated: bool) -> None:
    """H9 judge: persist ONE evidence artifact for the out-of-band `flow audit` to judge later. ZERO LLM, and
    fully swallowed — a capture problem must never fail (or slow the failure of) a replay. The page text is
    free: `report.final_text` was already captured by the engine on every replay."""
    try:
        from . import audit  # lazy: keeps the module off the import path of a flow that never audits

        doc = load_history(cache, key)
        rings, anchors = doc.get("fields") or {}, doc.get("anchors") or {}
        mfields = magnitude_fields(eff, data)
        signals = audit.drift_signals(rings, anchors, mfields)
        healed = int(getattr(report, "healed_steps", 0) or 0)
        rmode = getattr(report, "mode", "replay") or "replay"
        if not audit.should_capture(rmode, healed, runs=meta.runs, due=bool(meta.audit_due),
                                    signals=signals):
            return
        watched = {p: rings.get(p, []) for p in mfields}
        audit.capture(cache, key, goal=spec.goal, extract=spec.extract, data=data,
                      page_text=getattr(report, "final_text", "") or "",
                      rings=watched, anchors={p: anchors[p] for p in watched if p in anchors},
                      signals=signals, report_mode=rmode, healed=healed, truncated=truncated,
                      redact=_secret_values(spec))   # `capture` prunes the store itself
        if meta.audit_due:  # the due flag is CONSUMED by the capture it asked for
            _update_meta(cache, key, lambda m: setattr(m, "audit_due", False))
    except Exception as exc:  # noqa: BLE001 - audit capture is best-effort, never load-bearing
        _log.warning("flow %r: audit capture skipped (%s: %s)", spec.name, type(exc).__name__, exc)


def _magnitude_gate(cache: FlowCache, key: str, eff: dict, data: Any, spec_name: str) -> Optional[str]:
    """H9 layer 2 (pure replay only): for each in-scope scalar-number field, check the new value against its
    rolling baseline. An ENFORCED violation (baseline warmed AND not advisory) returns a value-free reason (the
    caller quarantines) and does NOT accrue — so a wrong value never poisons the baseline. A clean OR still-
    advisory run accrues every field. Returns the first enforced-violation reason, else None."""
    mfields = magnitude_fields(eff, data)
    if not mfields:
        return None
    rings = load_history(cache, key).get("fields", {})
    for path, value in mfields.items():
        c = eff.get(path, {})
        reason = check_magnitude(c, value, rings.get(path, []))
        if reason is None:
            continue
        n = len(rings.get(path, []))
        warmup = DELTA_WARMUP if c.get("warmup_runs") is None else c["warmup_runs"]
        if n >= warmup and not c.get("delta_advisory"):
            return f"field {path!r}: {reason}" if path else reason   # enforced -> quarantine; do NOT accrue
        _log.info("flow %r: magnitude ADVISORY (%s, n=%d) — %s", spec_name, path or "<root>", n, reason)
    _update_history(cache, key, lambda doc: _accrue_all(doc, mfields))
    return None


def is_write_flow(spec: FlowSpec, cached_flow) -> bool:
    """Does this flow WRITE? DECLARED (`spec.mutate`) or its cached steps in fact mutate.

    THE single definition. It was transcribed independently in `mcpserver._is_write_flow`, `run_batch` and
    `cli._flow_approve_all` — three copies of a predicate that decides whether an irreversible action gets a
    human in the loop, so a future upgrade of the write SIGNAL would have to find all three. (This release
    is such an upgrade: `_author_steps` now promotes a wire-attributed write onto its step, which changes
    what `s.mutating` means and therefore what all three of those callers do.)"""
    if spec.mutate is not None:
        return True
    return cached_flow is not None and any(getattr(s, "mutating", False) for s in cached_flow.steps)


def _approval_recipe_stale(meta: FlowMeta, cached_flow) -> Optional[str]:
    """Does the approval still bind the steps on disk? `None` = yes; otherwise a human-readable reason.

    THE single definition of the recipe-integrity predicate. It had three independent transcriptions —
    `_preflight_row` (the real gate), `health()` (the fleet view) and `dry_run` (the preview) — and `dry_run`'s
    copy dropped the `steps_hash is None` arm, so a flow approved by a pre-0.60 version produced an artifact
    byte-identical to a fully-blessed flow's (`approval_gates_skipped == []`) while the real pre-flight
    refused it. A preview strictly more permissive than the thing it previews is the worst kind, because it
    is what an operator plans around. Three arms, all of which must count as stale:
      * no digest at all (approved before the binding existed) — needs one human re-approval;
      * an UNCOMPUTABLE digest (a corrupt / hand-edited / foreign cache file) — cannot show it matches;
      * a digest that differs — something re-authored the recipe.
    """
    if not (meta.approved and cached_flow is not None):
        return None
    if meta.steps_hash is None:
        return ("approved by an older version that did not bind the approval to the flow's steps — the "
                "cached steps were never verified against the approval")
    try:
        current = steps_hash(cached_flow)
    except Exception as exc:  # noqa: BLE001 — a digest we cannot COMPUTE is not a digest that matches
        return f"could not verify the flow's steps against the approval ({type(exc).__name__}: {exc})"
    if current != meta.steps_hash:
        return f"the flow's steps changed since approval (approved {meta.steps_hash}, now {current})"
    return None


def health(spec: FlowSpec, *, cache: Optional[FlowCache] = None, stale_after: Optional[float] = None) -> FlowHealth:
    """A flow's status for the fleet view: not-learned / never-run / healthy / failing / stale.

    `stale_after` (seconds): a flow whose last success is older than this counts as `stale`.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    # `health()` backs `flow status` and the MCP tools/list loop, so ONE unreadable flow must not take down
    # the fleet view — but it must not read as "not learned" either, which is what silently flattening it
    # to None would do. Surface it as its own status.
    try:
        cached_flow = cache.get(key)
    except CacheUnreadableError as exc:
        _log.error("flow %r: cached recipe unreadable — %s", spec.name, exc)
        return FlowHealth(name=spec.name, status="unreadable", cached=False, approved=False,
                          runs=0, successes=0, consecutive_failures=0, last_run_ts=0.0, last_ok_ts=0.0,
                          last_error=f"cached flow unreadable: {exc}")
    cached = cached_flow is not None
    meta = _load_meta(cache, key)
    # Is the approval still bound to the steps on disk? THE shared predicate, so the fleet view can SHOW a
    # flow that will refuse instead of leaving an operator to discover it at run time. It never raises —
    # `health()` backs `flow status` and the MCP `tools/list` loop, so one corrupt flow must not take down
    # the fleet view.
    approval_stale = _approval_recipe_stale(meta, cached_flow) is not None
    if not cached:
        status = "not-learned"
    elif meta.quarantine is not None:  # H9: a wrong-value quarantine is the most severe/actionable state
        status = "quarantined"
    elif meta.runs == 0:
        status = "never-run"
    elif meta.consecutive_failures > 0:
        status = "failing"
    elif stale_after is not None and meta.last_ok_ts and (time.time() - meta.last_ok_ts) > stale_after:
        status = "stale"
    else:
        status = "healthy"
    return FlowHealth(
        name=spec.name, status=status, cached=cached, approved=meta.approved,
        runs=meta.runs, successes=meta.successes, consecutive_failures=meta.consecutive_failures,
        last_run_ts=meta.last_run_ts, last_ok_ts=meta.last_ok_ts, last_error=meta.last_error,
        approval_stale=approval_stale,
    )


async def _attempt_replay(spec, router, cache, key, meta, check_shape, *, mode="replay", provider=None,
                          params=None):
    """One replay attempt. Returns (ok, data, reason, kind).

    `kind` classifies a failure for the typed taxonomy: "" (ok) | "miss" | "escalate" | "shape" |
    "drift" (page/locator/not-found — the do-not-retry default). See `_classify_replay_failure`.

    `mode="replay"` is a pure 0-LLM run. `mode="repair"` additionally lets the engine self-heal /
    suffix-replan a drifted step in place (re-authoring just the broken tail, preserving the working
    prefix) using `provider` — used as a cheaper step before a full re-learn on `on_drift="relearn"`.
    `params` (H3) are the pre-validated per-run slot values substituted at the fill/type/select sites.
    """
    out: dict = {}
    # A learned pin anchors the OLD final page; a repaired flow may end elsewhere, so only trust the
    # pin on a pure replay — let the LLM extractor re-read the live value when we re-plan the tail.
    pin = meta.read_pin if (spec.pin_read and mode == "replay") else None
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=provider, cache=cache, mode=mode,
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state, params=params,
        finalize=_make_finalize(spec, router, out, pin=pin, redact=_secret_values(spec)),
        pre_write=_make_pre_write(spec, out),
        redact=_secret_values(spec),   # B2: never ship a resolved secret to a provider
    )
    if report.mode == "miss":
        return False, None, "no learned flow — run learn first", "miss"
    if not report.success:
        # An interstitial/CAPTCHA wall comes back as mode="escalate" — a distinct KIND (human needed),
        # not ordinary locator drift.
        kind = "escalate" if report.mode == "escalate" else "drift"
        return False, None, f"replay failed (page drift?): {report.note or report.mode}", kind
    if (spec.extract is not None or spec.mutate is not None) and not out.get("found"):
        # a write flow gates `found` on the confirm check, so an unconfirmed write fails here
        if out.get("confirm_pre_true"):
            # NOT "drift". Drift means the run did not get where it meant to; here the commit ACTUATED and
            # the confirm simply cannot report on it. Kept a distinct kind so it can never be re-driven:
            # `relearn` would re-author a flow that already fired, and an auth-refresh retry would fire it
            # again. Deliberately NOT `landed` — we do not KNOW it committed, and ledger.py's invariant is
            # "never a false skip of an un-landed write", so a keyed retry is the safer side of that trade.
            return False, None, f"{out.get('error')}", "write_unverified"
        return False, None, f"data not found / write not confirmed on replay: {out.get('error')}", "drift"
    if spec.mutate is not None and spec.extract is not None and not out.get("extract_found"):
        # The write CONFIRMED (above) but its readback missed. A distinct KIND from "drift", because the
        # remedy is the opposite one: the side effect already landed, so this must never be retried or
        # re-learned. Returning the confirm as a clean success here would hand the caller `data=None` for
        # a real order.
        why = out.get("extract_error") or "the value was not on the confirmation page"
        if out.get("truncated"):
            why += " (the page text was truncated before extraction, so the value may be past the cut)"
        return (False, None,
                f"the write WAS confirmed and must NOT be retried, but its confirmation readback failed: "
                f"{why}", "write_unreadable")
    data = out.get("data")
    if check_shape and meta.shape is not None and not _shape_matches(meta.shape, _shape_of(data)):
        return False, None, f"data shape changed vs the learned flow (expected {meta.shape})", "shape"
    # H9 VALUE checks (deterministic, 0-LLM). READ flows only (a write flow's meta.contracts stays None),
    # gated on the same `check_shape` trust switch as the shape gate. Both layers quarantine identically.
    if check_shape and spec.mutate is None:
        eff = effective_contracts(spec.contracts, meta.contracts)
        # Layer 1: same-shape VALUE check (type / null / sign / format / count-floor / null-rate).
        if eff:
            reason = check_contracts(eff, data, truncated=bool(out.get("truncated")))
            if reason is not None:
                return False, None, reason, "quarantine"
        # Layer 2: deterministic MAGNITUDE defense (scalar numbers vs a rolling baseline) — catches a wrong-but-
        # same-sign move like 129→40. PURE replay ONLY: a mode=="repair" suffix-replan intermediate never
        # accrues or fires (a re-authored baseline is reset on learn/relearn). Runs after layer 1.
        if mode == "replay":
            reason = _magnitude_gate(cache, key, eff, data, spec.name)
            if reason is not None:
                return False, None, reason, "quarantine"
        # H9 judge: capture an evidence artifact for the LATER, out-of-band `flow audit`. This writes a file;
        # it NEVER calls an LLM, never blocks, and never fails a replay. Only reached once BOTH deterministic
        # gates passed (a run that already quarantined needs no judge and its artifact would be the most
        # sensitive thing we could keep) and only on a pure replay.
        if mode == "replay" and spec.audit:
            _capture_audit(cache, key, spec, meta, report, data, eff=eff,
                           truncated=bool(out.get("truncated")))
    return True, data, "", ""


def _validate_one(spec: FlowSpec, name: str, slot: SlotSpec, value: Any) -> Any:
    """Validate one non-secret param value against its SlotSpec (pure, 0-LLM). Raises FlowReplayError."""
    def bad(why: str):
        return ParamValidationError(f"{spec.name!r}: param {name!r} {why} (got {value!r})")

    t = slot.type
    if t in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad(f"must be a {t}")
        # NaN/Inf slip past min/max (every ordering comparison with NaN is False; Inf beats any bound), so a
        # bounded slot would silently accept them and type "nan"/"inf" onto the page — refuse loud. Guard on
        # `float` ONLY: a Python int is always finite, and `math.isfinite(huge_int)` raises OverflowError
        # (not a FlowReplayError) — that would crash a batch instead of reporting the row invalid.
        if isinstance(value, float) and not math.isfinite(value):
            raise bad(f"must be a finite {t}")
        # An integer slot rejects a non-integer FLOAT; a Python int is always an integer (and `float(huge
        # int)` would OverflowError), so never convert an int here.
        if t == "integer" and isinstance(value, float) and not value.is_integer():
            raise bad("must be an integer")
        # Normalize an integer-VALUED float (e.g. JSON parses 2.0 to a float) to int, for BOTH slot types,
        # so two numerically-equal rows (2 and 2.0) fold to ONE idempotency key (no int-vs-float double-
        # write) and substitution types "2", not "2.0".
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if slot.min is not None and value < slot.min:
            raise bad(f"must be >= {slot.min}")
        if slot.max is not None and value > slot.max:
            raise bad(f"must be <= {slot.max}")
    elif t == "boolean":
        if not isinstance(value, bool):
            raise bad("must be a boolean")
    else:  # string (default)
        if not isinstance(value, str):
            raise bad("must be a string")
        if slot.max_length is not None and len(value) > slot.max_length:
            raise bad(f"must be at most {slot.max_length} chars")
        if slot.pattern is not None and re.fullmatch(slot.pattern, value) is None:
            raise bad(f"must match pattern {slot.pattern!r}")
    if slot.enum is not None and value not in slot.enum:
        raise bad(f"must be one of {slot.enum}")
    return value


def validate_params(spec: FlowSpec, params: Optional[dict]) -> dict:
    """H3 pre-flight (pure, 0-LLM): validate `params` against `spec.slots`, resolve secret slots from the
    env, and return the RESOLVED value dict to substitute at replay. Raises `FlowReplayError` on any
    violation BEFORE the browser opens — an out-of-domain value is a loud refusal, never a silent wrong
    value. Unknown param names (typo / stale schema) are refused; a required slot with no value refused; a
    secret slot's value must come from `$secret_env`, never `params`, and is never returned to a caller-
    visible surface beyond the substitution dict.

    `required` is enforced only when the caller is actually parameterizing (`params` is a dict, even
    empty). `params is None` means "replay the frozen flow" — no required check, non-secret slots keep
    their frozen literals — but SECRET slots always resolve from the env (a demo secret must never
    replay as a frozen plaintext literal)."""
    parameterizing = params is not None
    params = params or {}
    slots = spec.slots or {}
    unknown = [k for k in params if k not in slots]
    if unknown:
        raise ParamValidationError(
            f"{spec.name!r}: unknown param(s) {unknown} — the flow's slots are {sorted(slots)}")
    resolved: dict = {}
    for name, slot in slots.items():
        if slot.secret:
            if name in params:
                raise ParamValidationError(
                    f"{spec.name!r}: secret slot {name!r} must not be passed in params — it is read from "
                    f"${slot.secret_env}")
            val = os.environ.get(slot.secret_env or "")
            if val is None:
                if slot.required:
                    # An UNSET env var is an operator-config gap, NOT a caller-fixable argument -> base
                    # replay_error (the caller can't fix it by changing arguments).
                    raise FlowReplayError(
                        f"{spec.name!r}: secret slot {name!r} needs env var {slot.secret_env!r} set")
                continue
            resolved[name] = val
        elif name in params:
            resolved[name] = _validate_one(spec, name, slot, params[name])
        elif parameterizing and slot.required:
            raise ParamValidationError(f"{spec.name!r}: missing required param {name!r}")
        # else (frozen replay, or a non-required slot with no value) -> the step keeps its frozen literal
    return resolved


def _preflight_row(
    spec: FlowSpec, params: Optional[dict], *, meta: "FlowMeta", cached_flow: Optional[CachedFlow],
    require_approved: bool = False, on_drift: str = "raise",
    skip_approval_gates: bool = False,
) -> dict:
    """The 0-LLM, NO-BROWSER trust gate shared by `replay()` and `run_batch()`: resolve + validate one
    row's params against the slot schema and run every guard with ZERO side effects, returning the
    RESOLVED substitution dict (caller params + env-resolved secrets). Raises `FlowReplayError` on any
    violation. Single source of truth — a batch validates every row through this BEFORE actuating any, and
    each actuation re-runs it inside `replay()`, so a guard change lands once and both paths inherit it.

    Guards, in order: 0-LLM pre-flight (`validate_params`); a write needs a confirm check; `on_drift=
    'relearn'` is refused for a write and for any parameterized replay (a re-author drops params → frozen
    defaults); the approval gate; the three approval BINDINGS — slot schema (a domain widened since approve()
    must re-approve), value contracts, and the RECIPE itself (`cache.steps_hash`: the cached steps must still
    be the ones a human reviewed); the binding guard (a supplied slot must bind a type/select step, else its
    value folds into the idempotency key without being typed); the precheck refusal (a parameterized write
    can't lean on a row-blind one-shot precheck)."""
    is_mutate = spec.mutate is not None
    parameterizing = params is not None
    # H9: a quarantined flow refuses EVERY future run, 0-LLM, before any browser or arg validation — a
    # persisted wrong-value quarantine dominates (a bad arg is moot while the flow is known to return wrong
    # data). Cleared only by a human `release()` after investigating the value. `replay`, `run_batch` (per-row),
    # `preflight_keys` (MCP), and `run_all` all funnel through here, so every surface inherits the refusal.
    if meta.quarantine is not None:
        raise FlowQuarantineError(
            f"{spec.name!r}: quarantined — {meta.quarantine.get('reason')}. Investigate the value, then "
            f"`flow release` (or relax the contract via spec.contracts + re-approve).")
    resolved = validate_params(spec, params)  # 0-LLM pre-flight: raises on any out-of-domain value
    if is_mutate:
        if not spec.mutate.has_confirm():
            raise FlowReplayError(
                f"{spec.name!r}: a write flow needs a confirm check — set "
                f"mutate.confirm_selector / confirm_text_contains / confirm_url_contains")
        if on_drift == "relearn":
            raise FlowReplayError(
                f"{spec.name!r}: on_drift='relearn' is refused for a write flow (re-authoring would "
                f"re-perform the write) — re-learn manually and re-approve instead")
        # THE MACHINE CORRECTNESS FLOOR. Every retry-dedupe guarantee this project makes rides on the
        # per-write Idempotency-Key, and `flow._replay_step` only sets that header for a `mutating` step.
        # A DECLARED write whose commit no step is marked mutating for therefore plans ZERO keys — and
        # every mechanism built on them silently no-ops: the key never reaches the wire, `RunLedger`
        # short-circuits on an empty key list so no ledger file is ever created, `run_batch(resume=...)`
        # re-fires the commit on every resume with no human in the loop, and `dry_run` — which releases
        # idempotent methods unheld — ACTUALLY FIRES a GET-link commit at the real server while reporting
        # `writes_planned=0` and certifying itself clean. Refuse at the shared gate so all four surfaces
        # inherit it. NOT fixed by synthesizing a key in `_plan_idempotency_keys`: its contract is
        # byte-identity with the header the wire actually carries, and a preview of a key that is never
        # sent is worse than an honest empty one.
        if cached_flow is not None and not any(s.mutating for s in cached_flow.steps):
            raise UnkeyedWriteError(
                f"{spec.name!r}: declared a WRITE, but no recorded step is classified as mutating — so it "
                f"would fire with NO Idempotency-Key and NO ledger entry, and a client retry or a resumed "
                f"batch would re-fire it. Re-record it (`flow record --name {spec.name}`) so the commit is "
                f"captured as a real write; if the commit is a bare GET link, it cannot be made "
                f"retry-safe automatically and needs a human-gated surface.")
    # A relearn re-authors the flow from scratch, which does NOT carry `params` — the re-authored flow has
    # no slot-bound steps, so it would run the DEFAULT values and return data for them, silently ignoring the
    # per-run params (a silently-wrong read, inviolable #2). Refuse the combination rather than mislead.
    if parameterizing and on_drift == "relearn":
        raise FlowReplayError(
            f"{spec.name!r}: on_drift='relearn' can't be combined with params — a re-author ignores the "
            f"per-run values and would return data for the frozen defaults. Use on_drift='fail' (the "
            f"default) with params, or drop params to relearn the frozen flow.")
    # A write defaults to approval-gated even without require_approved (stronger trust for writes).
    # `skip_approval_gates` is plumbed ONLY from `flows.dry_run`, which is the pre-approval
    # artifact — gating it on approval would make it useless for the case it exists for. Note
    # `require_approved=False` is NOT sufficient: the gate is unconditional for a WRITE flow.
    # Every skipped gate is NAMED in the report, so a dry run of an unapproved or re-authored
    # recipe can never be mistaken for a run of an approved one.
    if not skip_approval_gates and (require_approved or is_mutate) and not meta.approved:
        raise FlowReplayError(f"{spec.name!r}: flow not approved — learn it, verify it, then approve")
    # The approval is bound to the slot schema. A slotted flow whose domain changed since approve() (e.g. a
    # payee enum loosened to any string) must refuse until re-approved — a stale approval must never
    # authorize a WIDER contract than the human reviewed (an injection surface, worst on a write).
    # NO `spec.slots and` short-circuit — for exactly the reason spelled out on the contracts gate below.
    # With it, removing ONE slot correctly refused while removing the WHOLE TABLE passed: `_slots_hash`
    # returns None for an empty table, so the comparison was skipped entirely. A scrubbed secret step then
    # types the EMPTY STRING into a credential field before the submit, and the write mints a different
    # Idempotency-Key than the same logical write did while slotted.
    if not skip_approval_gates and meta.approved and _slots_hash(spec) != meta.slots_hash:
        raise FlowReplayError(
            f"{spec.name!r}: the slot schema changed since approval — re-approve the flow before replaying "
            f"it (a widened/edited slot domain must not run under a stale approval)")
    # H9: the human value-contract overlay is likewise approval-bound. A tightened OR loosened OR fully
    # REMOVED contract since approve() must be re-blessed (a loosened/removed contract is a WEAKENED fail-loud
    # guarantee). No `spec.contracts and` short-circuit — else dropping the whole overlay would silently skip
    # the re-approval. Base replay_error (a config re-bless, not a data quarantine). The machine seed is
    # learn-bound, not hashed, so it can never silently widen a human-approved guarantee.
    if not skip_approval_gates and meta.approved and _contracts_hash(spec) != meta.contracts_hash:
        raise FlowReplayError(
            f"{spec.name!r}: value contracts changed since approval — re-approve the flow before replaying it "
            f"(a tightened or loosened value guarantee must be re-blessed by a human)")
    # RECIPE INTEGRITY — the gate that makes `approved` mean what a human thinks it means. The two gates above
    # bind the SPEC; this one binds the STEPS. Without it `approved` was a sticky bit that survived the flow
    # being re-authored underneath it: a self-heal that retargets a locator, a suffix-replan, a `flow record`
    # over an approved flow, a re-learn, or a hand-edited cache file all left `approved=True` on steps no human
    # ever read — and on a WRITE that means firing an unreviewed action at an unreviewed target. Checked here,
    # pre-browser, so the refusal costs nothing and nothing has acted.
    # NOT back-filled when there is no digest: back-filling would stamp whatever the steps are NOW as
    # "approved", rubber-stamping precisely the silently-re-authored recipe the gate exists to catch. One
    # human re-approval per flow, once. And an UNCOMPUTABLE digest refuses inside the taxonomy rather than
    # escaping as a bare TypeError a caller's `except FlowReplayError` would miss.
    if not skip_approval_gates:
        stale = _approval_recipe_stale(meta, cached_flow)
        if stale is not None:
            raise StaleApprovalError(
                f"{spec.name!r}: {stale} — refusing to replay steps no human reviewed. Review them "
                f"(`flow inspect --name {spec.name}`) and re-approve (`flow approve --name {spec.name}`). "
                f"This is never done automatically: it would bless whatever the steps happen to be now.")
    # APPROVAL INTEGRITY: `on_drift="relearn"` lets the engine SELF-HEAL / suffix-replan / fully re-author the
    # cached steps and write them straight back (`flow.py`'s `if dirty and success: cache.put(flow)`). On an
    # APPROVED flow that would run LLM-authored steps no human ever reviewed, under the existing approval — the
    # human sign-off this project advertises would not be binding. It also silently re-baselines H9 (a re-learn
    # re-seeds shape/contracts and resets the magnitude history). So refuse, exactly as a write flow already
    # does: recovery on an approved flow is a human action (`flow record`/`flow learn`, then re-approve).
    if not skip_approval_gates and meta.approved and on_drift == "relearn":
        raise FlowReplayError(
            f"{spec.name!r}: on_drift='relearn' is refused for an APPROVED flow — re-authoring would replay "
            f"steps no human reviewed under the existing approval (and would re-baseline the value contracts). "
            f"Fix it deliberately: `flow record`/`flow learn`, review with `flow inspect`, then `flow approve`. "
            f"To let an unattended run self-heal, run the flow unapproved (`--include-unapproved`).")
    # BINDING SAFETY: a resolved slot value only substitutes at a step whose `.slot` names it. If a supplied
    # slot binds to NO recorded type/select step, its value would fold into the flow's identity — and, at a
    # write, into the Idempotency-Key on the wire — WITHOUT being typed onto the page: the frozen literal is
    # submitted while the key varies per value (a silent wrong write + an un-dedup-able double write; for a
    # read, silently wrong data). Count only type/select bindings — a `press` carries a KEY, never a value,
    # so a slot bound to a press/click/navigate step would satisfy a looser check yet never substitute.
    if resolved and cached_flow is not None:
        bound = {s.slot for s in cached_flow.steps if s.slot and s.action in ("type", "select")}
        unbound = sorted(k for k in resolved if k not in bound)
        if unbound:
            raise FlowReplayError(
                f"{spec.name!r}: slot(s) {unbound} were supplied but aren't bound to any recorded "
                f"type/select step — the value would change the flow's idempotency key without being "
                f"entered on the page (a silent wrong/duplicate action). Bind each slot to the step it "
                f"fills before replaying.")
    # PRECHECK SAFETY: the one-shot idempotency precheck (`mutate.precheck_*`) probes a FIXED url/marker with
    # NO row awareness. On a PARAMETERIZED write a generic end-state left by one row would make a DIFFERENT
    # row's write skip as "already-done" — a silently suppressed write. Its retry-safety is the row-keyed key.
    if is_mutate and parameterizing and spec.mutate.has_precheck():
        raise FlowReplayError(
            f"{spec.name!r}: a parameterized write can't use a one-shot precheck (mutate.precheck_*) — the "
            f"precheck is row-blind and could skip a distinct row's write as already-done. Remove the precheck "
            f"(the row-keyed Idempotency-Key gives retry-dedup safety), or replay this row without params.")
    return resolved


def _plan_idempotency_keys(spec: FlowSpec, resolved: dict, cached_flow: CachedFlow) -> list[str]:
    """The Idempotency-Key(s) a parameterized WRITE row WOULD mint at actuation — computed with the SAME
    four inputs as `flow._replay_step` (scope=`spec.scope`, idx=the `enumerate` index, intent=`step.intent`,
    slot_values=`resolved`), so a dry-run preview is byte-identical to the wire key. Empty for a read (no
    mutating step)."""
    return [idempotency_key(spec.scope, i, s.intent, slot_values=resolved)
            for i, s in enumerate(cached_flow.steps) if s.mutating]


def preflight_keys(
    spec: FlowSpec, params: Optional[dict], *, cache: FlowCache, require_approved: bool = True,
) -> "tuple[dict, list[str]]":
    """Pure, 0-LLM, ZERO side-effect pre-flight: resolve + validate one row's `params` through EVERY trust
    guard (`_preflight_row`: approval, schema-hash, slot binding, precheck-refusal, …) and return
    `(resolved, the write's Idempotency-Key(s))`. Raises `FlowReplayError` (incl. `ParamValidationError`) on
    any violation. `keys == []` for a read. The public entrypoint the MCP write surface uses to check the
    dedupe ledger + build a confirm preview BEFORE actuating — without reaching into the private helpers."""
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    cached = cache.get(key)
    resolved = _preflight_row(spec, params, meta=meta, cached_flow=cached,
                              require_approved=require_approved, on_drift="raise")
    return resolved, (_plan_idempotency_keys(spec, resolved, cached) if spec.mutate is not None else [])


async def dry_run(spec: FlowSpec, params: Optional[dict] = None, *,
                  cache: Optional[FlowCache] = None) -> "DryRunReport":
    """Replay a WRITE flow with every write HELD — the pre-approval artifact.

    Shows the exact request bodies this flow *would* send, with the mutation gate fully armed, on a site
    with no staging environment. The hold guarantee lives in `dryrun.DryRunArbiter`; this is the driver.

    A THIN DRIVER, NOT A FLAG THROUGH `replay()`, deliberately: `replay()`'s tail records run health on
    every path (`_record_run` on success, drift and exception). Threading a flag through it would leave a
    future edit one line away from silently recording a dry run as a real one — and a dry run is neither a
    success (the flow was never shown to work) nor a failure (a hold is the intended outcome). `health()`
    must be byte-identical before and after. So: no `_record_run`, ever, on any path here.

    What is DELIBERATELY BYPASSED — and every one is named in the report, so a dry run of an unapproved or
    re-authored recipe can never be mistaken for a run of an approved one:
      * the approval gates. This IS the pre-approval artifact; gating it on approval makes it useless for
        the only case it exists for. Note `require_approved=False` is NOT sufficient — the gate is
        UNCONDITIONAL for a write flow (`require_approved or is_mutate`), which is every flow that matters
        here.
      * `_already_committed` (the one-shot idempotency precheck): it opens a SECOND BrowserSession with no
        arbiter attached. GET-only today, but an unprotected browser in a mode that promises none exist is
        not a risk worth carrying — and an "already-done" short-circuit would return with no artifact.
      * auth refresh: `_form_login` submits real credentials to a real server. Under the arbiter that POST
        is HELD, so login silently fails and surfaces as bogus drift. A dry run therefore needs a valid
        `spec.storage_state` and says so by name when it lands on a login page.

    What STILL RUNS, because these are what make the artifact predictive rather than a simulation: the
    mutation gate, the H9 quarantine refusal, `validate_params`, the write-needs-a-confirm-check refusal,
    both `on_drift='relearn'` refusals, the slot-binding guard, and the parameterized-write precheck
    refusal.
    """
    from .dryrun import DryRunArbiter

    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    cached = cache.get(key)
    arb = DryRunArbiter()
    rep = arb.report

    if cached is None:
        rep.aborted = "not-learned"
        rep.abort_detail = f"{spec.name!r}: nothing cached — learn or record the flow first"
        return rep
    try:
        rep.steps_hash = steps_hash(cached)
    except Exception:  # noqa: BLE001 — an uncomputable digest is REPORTED below, never raised past the API
        rep.steps_hash = ""   # `health()` and pre-flight both treat "cannot compute" as stale; so must this
    rep.writes_planned = sum(1 for s in cached.steps if s.mutating)
    if not meta.approved:
        rep.approval_gates_skipped.append("not-approved (this is the pre-approval artifact)")
    # THE shared predicate — not a fourth transcription of it. Its `steps_hash is None` arm is the one this
    # report used to drop, which made a pre-0.60 migration approval preview as fully blessed.
    recipe_stale = _approval_recipe_stale(meta, cached)
    if recipe_stale is not None:
        rep.approval_gates_skipped.append(f"stale_approval: {recipe_stale}")
    # NO `spec.slots and` short-circuit, for the reason spelled out on `_preflight_row`'s slot gate: with it,
    # removing ONE slot was reported while removing the WHOLE TABLE was not (`_slots_hash` returns None for an
    # empty table, so the comparison was skipped entirely) — the A13 hole, in the preview instead of the gate.
    if meta.approved and _slots_hash(spec) != meta.slots_hash:
        rep.approval_gates_skipped.append("the slot schema changed since approval")
    if meta.approved and _contracts_hash(spec) != meta.contracts_hash:
        rep.approval_gates_skipped.append("the value contracts changed since approval")

    try:
        resolved = _preflight_row(spec, params, meta=meta, cached_flow=cached,
                                  require_approved=False, on_drift="raise", skip_approval_gates=True)
    except FlowReplayError as exc:
        rep.aborted = getattr(exc, "code", "replay_error")
        rep.abort_detail = str(exc)
        return rep

    rep.precheck_skipped = spec.mutate is not None and spec.mutate.has_precheck()
    report = await run_cached(
        spec.start_url, spec.goal, None, cache, mode="replay", headless=spec.headless,
        scope=spec.scope, extra_headers=spec.headers, storage_state=spec.storage_state,
        params=resolved or None, dry_run=arb,
    )
    arb.reconcile()

    rep.writes_reached = sum(1 for h in rep.held if h.in_window)
    # The FIRST hold makes every later step unrepresentative: it was fulfilled with a synthesized response,
    # so the page state after it is fictional and write #2's body may be computed from it.
    first_hold_step = min((h.step for h in rep.held if h.step >= 0), default=-1)
    rep.steps_representative = first_hold_step if first_hold_step >= 0 else len(cached.steps)
    if rep.held and rep.writes_planned > rep.writes_reached:
        rep.warnings.append(
            f"only {rep.writes_reached} of {rep.writes_planned} planned writes were reached — the flow "
            f"stopped at the first held write, so the remaining bodies are UNKNOWN, not empty")
    if rep.held:
        rep.warnings.append(
            f"steps after index {rep.steps_representative} ran against a SYNTHESIZED response and are not "
            f"representative of a real run")
    if not report.success and rep.aborted is None and not rep.held:
        rep.warnings.append(f"replay did not complete: {report.note or report.mode}")
    return rep


async def replay(
    spec: FlowSpec, *, require_approved: bool = False, on_drift: str = "raise",
    check_shape: bool = True, auth_refresh: bool = True, provider_name: Optional[str] = None,
    provider=None, router=None, cache: Optional[FlowCache] = None, params: Optional[dict] = None,
) -> Any:
    """Replay the learned flow at 0-LLM navigation and return the extracted data.

    Trust controls for unattended use:
      - `require_approved=True` — refuse to run a flow that hasn't been `approve`d.
      - `check_shape=True` — treat a change in the data's *structure* (vs the learned run) as drift.
      - `auth_refresh=True` — on drift, if `spec.login` is set, re-login (refresh cookies) and
        retry once before giving up (handles an expired session).
      - `on_drift="raise"` (default) — raise `FlowReplayError` on any drift (never return wrong
        data); `on_drift="relearn"` — re-author the flow instead and return the fresh data.

    WRITE flows (`spec.mutate` set, Phase D) behave differently: they default to approval-gated
    (a write is human-verified before unattended runs), refuse `on_drift="relearn"` (re-authoring
    a write would re-perform it), verify the write landed (fail loud if not), and return a dict
    `{"status": "confirmed" | "already-done", "data": <optional extracted data>}`.

    `params={slot: value}` (H3 typed templates) substitutes validated per-run values into the flow's
    slot-marked fill/type/select/press steps (0-LLM pre-flight validation; `flow_key` unchanged, so
    values never enter identity). Read flows only in this slice — parameterizing a WRITE flow is
    refused (write templates + row-keyed idempotency are the next slice).
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    is_mutate = spec.mutate is not None
    parameterizing = params is not None  # caller opted into the param path (vs a frozen replay)
    # H3 slice 2a/2b: run the shared 0-LLM, NO-BROWSER preflight gate — resolve + validate this row's params
    # and run every trust guard (confirm-required, relearn-incompatibility, approval, schema-hash, slot
    # binding, precheck-refusal) with ZERO side effects, returning the resolved substitution dict.
    # `run_batch` calls the SAME `_preflight_row` per row before actuating any, so a guard change lands once
    # and both paths inherit it. (Parameterized WRITE flows ARE supported: each actuation folds the run's
    # slot values into a row-keyed Idempotency-Key — distinct rows -> distinct keys, a retry -> the same key.)
    cached_flow = cache.get(key)
    params = _preflight_row(spec, params, meta=meta, cached_flow=cached_flow,
                            require_approved=require_approved, on_drift=on_drift)

    # Idempotency precheck (opt-in, one-shot writes): if the end-state already holds, skip the write.
    if await _precheck_done(spec):
        _record_run(cache, key, ok=True)
        _log.info("flow %r: write already done (idempotency precheck) — skipped", spec.name)
        return {"status": "already-done", "data": None}

    if on_drift == "relearn":
        # relearn re-authors the flow, so it needs both an agent provider and an extraction router
        if provider is None or router is None:
            dp, dr = _router(provider_name or settings.provider)
            provider = provider if provider is not None else dp
            router = router if router is not None else dr
    elif router is None and spec.extract is not None and not (spec.pin_read and meta.read_pin):
        # extraction only (incl. a write that also extracts a confirmation number): build just the
        # router, no agent provider. Flows that don't extract (navigate-only reads, writes whose
        # confirm check is selector/url/text based, or a PINNED read) never call the LLM on replay
        # -> no router needed, and no API key required to run.
        router = build_router(provider_name or settings.provider)

    def _ok(data):
        _record_run(cache, key, ok=True)
        _log.info("flow %r: replay ok%s", spec.name, " (write confirmed)" if is_mutate else "")
        return {"status": "confirmed", "data": data} if is_mutate else data

    def _do_quarantine(why: str) -> "FlowQuarantineError":
        # H9: a value-contract violation is DETERMINISTIC wrong data — re-login / re-author can't fix it, and a
        # relearn would BYPASS the quarantine (re-seeding contracts from the wrong page). PERSIST it (every
        # future run then refuses 0-LLM at pre-flight) and fail loud. Called from BOTH the first attempt AND the
        # post-auth-refresh attempt, so a quarantine detected on the retry is never dropped without persisting.
        _quarantine(cache, key, reason=why)
        _record_run(cache, key, ok=False, error=why)
        _log.warning("flow %r: QUARANTINED — %s", spec.name, why)
        raise FlowQuarantineError(f"{spec.name!r}: {why}")

    try:
        ok, data, reason, kind = await _attempt_replay(spec, router, cache, key, meta, check_shape,
                                                        params=params)
        if ok:
            return _ok(data)
        if kind == "write_unverified":
            # The commit actuated. Raise HERE, before `retry_ok` exists, so it can reach neither the
            # auth-refresh retry nor the relearn path — both would fire it again. Recorded ok=FALSE (unlike
            # the readback case): we do not know the write landed, so this is a genuine failure a human
            # must look at, and a failure streak is the right signal.
            _record_run(cache, key, ok=False, error=reason)
            _log.error("flow %r: the commit actuated but cannot be verified — %s", spec.name, reason)
            raise WriteUnverifiedError(f"{spec.name!r}: {reason}")
        if kind == "write_unreadable":
            # The write LANDED; only its readback failed. Raise HERE, before `retry_ok` is even computed,
            # so this can enter neither the auth-refresh retry nor the relearn path — both would re-fire a
            # committed write. Recorded as ok=True on purpose: the write succeeded, and a failure streak
            # would push an operator toward the one action that must not be taken.
            _record_run(cache, key, ok=True)
            _log.error("flow %r: write confirmed but its readback FAILED — %s", spec.name, reason)
            raise WriteReadbackError(f"{spec.name!r}: {reason}")
        if kind == "quarantine":
            raise _do_quarantine(reason)   # first attempt: never enters the auth-refresh / relearn paths
        # The session may have expired — re-login (refresh cookies) and retry once. A WRITE flow is
        # NOT retried unless it has an idempotency precheck: a first attempt may have committed the
        # write before failing its confirm check, and a blind retry would double-submit. With a
        # precheck we re-check first and skip if the write already landed.
        # A write flow is retried after auth-refresh only if a re-run can't double-submit: a SINGLE-write flow
        # with a whole-flow precheck (re-check first, skip if already landed). A MULTI-WRITE flow is NEVER
        # auto-retried — a whole-flow precheck only models the LAST write, so a retry would re-fire an
        # already-landed earlier write (per-write resume that would make this safe is a deferred slice). It
        # fails loud instead, exactly as a single write without a precheck.
        retry_ok = auth_refresh and spec.login is not None and (
            not is_mutate or (spec.mutate.has_precheck() and not spec.mutate.is_multiwrite()))
        if retry_ok:
            try:
                await refresh_auth(spec, headless=spec.headless)
                if await _precheck_done(spec):  # the first attempt's write may have landed
                    _record_run(cache, key, ok=True)
                    return {"status": "already-done", "data": None}
                ok, data, reason2, kind2 = await _attempt_replay(spec, router, cache, key, meta,
                                                                 check_shape, params=params)
                if ok:
                    return _ok(data)
                reason = f"{reason}; after auth refresh: {reason2}"
                kind = kind2  # the post-refresh failure kind is the operative one now
            except Exception as exc:  # noqa: BLE001 - any refresh failure -> fall through to relearn/raise
                reason = f"{reason}; auth refresh failed: {type(exc).__name__}: {exc}"
        elif is_mutate and auth_refresh and spec.login is not None:
            if spec.mutate.is_multiwrite():
                reason = (f"{reason}; not retrying a MULTI-WRITE flow after auth refresh (a re-run would "
                          f"re-fire an already-landed earlier write; per-write resume is not yet supported) "
                          f"— run `flow login` then replay")
            elif parameterizing:
                # A parameterized write can't carry a precheck (row-blind — refused above), so don't advise
                # one; its retry-safety is the row-keyed Idempotency-Key (same row -> same key -> the backend
                # dedupes the re-run), so a manual re-run after re-login is safe.
                reason = (f"{reason}; not retrying a parameterized write after auth refresh (would risk a "
                          f"double-submit) — run `flow login` then replay this row; its row-keyed "
                          f"Idempotency-Key dedupes the re-run")
            else:
                reason = (f"{reason}; not retrying a write after auth refresh without an idempotency precheck "
                          f"(would risk a double-submit) — add mutate.precheck_* or run `flow login` then replay")
        if kind == "quarantine":
            # The post-auth-refresh attempt hit a value-contract violation — persist + fail loud BEFORE the
            # relearn block (a relearn would re-seed contracts from the wrong page and bypass the quarantine).
            raise _do_quarantine(reason)
        if on_drift == "relearn":  # (refused above for write flows)
            # The flow has drifted, so a previously-learned pin (anchored to the OLD final page) is no
            # longer trustworthy — drop it BEFORE we repair, and persist that first. The repair re-caches
            # a flow that may end on a different page; clearing the pin only AFTER that cache write would
            # leave a crash window where a stale pin could later be read against the new page. The repair
            # itself doesn't use the pin (it re-reads via the LLM extractor), so clearing early is safe;
            # a full re-learn below re-pins from scratch.
            if spec.pin_read and meta.read_pin is not None:
                meta.read_pin = None  # keep the in-memory snapshot consistent for the repair below
                _update_meta(cache, key, lambda m: setattr(m, "read_pin", None))
            # Cheapest repair first: re-author ONLY the broken tail from the current page, keeping the
            # working prefix (suffix-replan). This fixes locator/path drift without re-running the whole
            # flow. It can't fix data-SHAPE drift (the steps still replay) — that falls to a full relearn.
            ok, data, reason3, _kind3 = await _attempt_replay(
                spec, router, cache, key, meta, check_shape, mode="repair", provider=provider, params=params
            )
            if ok:
                _log.info("flow %r: drift repaired by suffix-replan (prefix preserved)", spec.name)
                return _ok(data)
            # Full re-author from scratch (also refreshes the sidecar metadata: shape, pin, approval).
            res = await learn(spec, provider=provider, router=router, cache=cache)
            if res.cached and res.found:
                return _ok(res.data)
            reason = f"replay drifted ({reason}); suffix-replan failed ({reason3}); re-learn failed ({res.note})"
        _record_run(cache, key, ok=False, error=reason)
        _log.warning("flow %r: replay FAILED — %s", spec.name, reason)
        raise _classify_replay_failure(kind)(f"{spec.name!r}: {reason}")
    except FlowReplayError:
        raise  # the failure above is already recorded in health
    except Exception as exc:  # noqa: BLE001 - an unexpected crash (browser/extract) is still a failed run
        _record_run(cache, key, ok=False, error=f"{type(exc).__name__}: {exc}")
        _log.warning("flow %r: replay crashed — %s: %s", spec.name, type(exc).__name__, exc)
        raise


# --- spec persistence (for the `ultracua flow` CLI) -------------------------------------------
class EmptyFlowStoreError(RuntimeError):
    """A FLEET verb resolved ZERO flows, so it would have reported success while doing nothing.

    This is the ops-layer form of inviolable #2 (never silently act wrong). The overwhelmingly common cause is
    a scheduled job or an MCP server started in the wrong working directory — the flow home is cwd-relative
    unless `$ULTRACUA_HOME` is set. Pass `allow_empty=True` (CLI: `--allow-empty`) where an empty fleet is
    genuinely expected."""


def _resolve_fleet(names: Optional[list], *, allow_empty: bool, verb: str) -> list:
    """The shared fleet-name resolver. An EXPLICIT `names=[]` is a caller who asked for nothing and gets
    nothing. But `names=None` (i.e. "every saved flow") resolving to ZERO is the silent-success footgun —
    fail loud instead, naming the resolved home so the diagnosis is in the error."""
    if names is not None:
        return list(names)
    found = list_specs()
    if not found and not allow_empty:
        raise EmptyFlowStoreError(
            f"{verb}: no flows found in {flow_home()} — refusing to report success while doing nothing. "
            f"Either you are running from the wrong directory (set $ULTRACUA_HOME, or run from the project "
            f"that owns .ultracua/), or this store is genuinely empty — pass --allow-empty if so.")
    return found


def _specs_dir() -> Path:
    """`<flow_home>/specs`. Resolved through `config.flow_home()` so the specs dir and the flow CACHE can
    never diverge, and so a run from a project subdirectory finds the project's flows (see `flow_home`)."""
    return flow_home() / "specs"


def _only_known(data: dict, cls) -> dict:
    """Drop keys that aren't fields of `cls` — so a spec written by another version still loads."""
    fields = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in fields}


def save_spec(spec: FlowSpec) -> Path:
    """Persist a flow spec as JSON under `.ultracua/specs/` (relative to cwd).

    Note: the spec records `storage_state` (a *path* to a cookies file), never credentials. The
    cookies file it points at is a live session — keep it out of version control (it's secret).
    """
    if callable(spec.login):
        raise ValueError("a callable `login` can't be saved — use a LoginSpec, or the library API")
    d = _specs_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{spec.name}.json"
    body = asdict(spec)
    # `asdict` doesn't recurse into the pydantic StepConfirm objects in mutate.step_confirms (they'd make
    # json.dumps raise) — serialize them explicitly.
    if spec.mutate is not None and spec.mutate.step_confirms:
        body["mutate"]["step_confirms"] = [sc.model_dump() for sc in spec.mutate.step_confirms]
    p.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return p


def load_spec(name: str) -> FlowSpec:
    p = _specs_dir() / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"no saved flow {name!r} (looked in {p})")
    data = _only_known(json.loads(p.read_text(encoding="utf-8")), FlowSpec)
    if isinstance(data.get("login"), dict):
        data["login"] = LoginSpec(**_only_known(data["login"], LoginSpec))
    if isinstance(data.get("mutate"), dict):
        m = _only_known(data["mutate"], MutateSpec)
        if isinstance(m.get("step_confirms"), list):  # rebuild the pydantic StepConfirm objects from dicts
            m["step_confirms"] = [StepConfirm(**sc) if isinstance(sc, dict) else sc for sc in m["step_confirms"]]
        data["mutate"] = MutateSpec(**m)
    if isinstance(data.get("slots"), dict):  # H3: rebuild SlotSpec objects from their serialized dicts
        data["slots"] = {k: SlotSpec(**_only_known(v, SlotSpec)) if isinstance(v, dict) else v
                         for k, v in data["slots"].items()}
    return FlowSpec(**data)


def list_specs() -> list[str]:
    d = _specs_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


async def serve_mcp(cache: Optional[FlowCache] = None, *, name: str = "ultracua") -> None:
    """H2 stage 1: run a stdio MCP server exposing every APPROVED READ flow as one deterministic,
    zero-argument tool (write flows are default-deny). Each tool call dispatches to `replay(...)` —
    the safety-gated path — so an MCP client (Claude, Cursor, VS Code, …) gets one verified result
    instead of LLM-driving a browser step by step. Needs the optional `mcp` SDK (`uv sync --group
    mcp`). Blocks until the client disconnects."""
    from .mcpserver import serve

    await serve(cache, name=name)


# --- fleet supervisor (Phase E) ---------------------------------------------------------------
@dataclass
class FleetRun:
    """One flow's outcome in a fleet run (`run_all`)."""

    name: str
    ok: bool
    status: str           # "ok" | "failed" | "skipped"
    ms: float = 0.0
    data: Any = None
    error: Optional[str] = None


@dataclass
class AuditFinding:
    """One judged artifact (H9). `code` is a key of `audit.REASONS`; `enforced` says whether it quarantined
    (only ever true in `enforce` mode with a code-verified anchor)."""

    name: str
    code: str
    enforced: bool
    ts: float = 0.0
    quote: str = ""       # the model's citation — for `flow audit --show`; NEVER persisted to meta


@dataclass
class AuditRun:
    """The report from one `flow audit` invocation."""

    findings: list = field(default_factory=list)     # list[AuditFinding]
    judged: int = 0
    unjudged: int = 0          # artifacts left on disk (budget exhausted / no LLM) — NOT "clean"
    quarantined: int = 0
    advisories: int = 0
    calls: int = 0
    skipped: list = field(default_factory=list)      # [(name, why)]
    budget_exhausted: bool = False
    no_llm: bool = False

    @property
    def exit_code(self) -> int:
        """2 = something was quarantined (alert); 3 = artifacts left UNJUDGED ("we didn't look" — a weaker,
        distinct alarm); 0 = nothing to report. 2 wins if both apply."""
        if self.quarantined:
            return 2
        return 3 if (self.unjudged or self.no_llm) else 0


async def audit_flows(
    *, names: Optional[list[str]] = None, cache: Optional[FlowCache] = None, router=None,
    provider_name: Optional[str] = None, max_calls: int = 0, dry_run: bool = False, keep: bool = False,
    allow_empty: bool = False,
) -> AuditRun:
    """H9 judge — the ASYNC, out-of-band audit verb. Reads persisted artifacts, asks the judge, and lets
    PURE code decide. It opens NO browser, never calls `replay`, and is never reached from the replay path.

    THE GUARANTEE: the only state it can write is `FlowMeta.quarantine` (via `QuarantineSink`, which takes a
    CODE and looks the English up in a closed table) and the `audit_advisories` counter. It cannot approve,
    clear, un-fail, rebaseline, edit contracts, or touch a write flow — those capabilities are never
    constructed. A flow that is ALREADY quarantined is skipped before any LLM call, so an audit can never
    overwrite (and thereby soften) a deterministic layer-1/2 reason."""
    from . import audit

    cache = cache or _default_cache()
    run = AuditRun()
    max_calls = max_calls or audit.AUDIT_MAX_CALLS
    if router is None:
        pname = provider_name or settings.provider
        if not _llm_configured(pname):
            run.no_llm = True
        else:
            _, router = _router(pname)

    # gather candidate artifacts across flows, riskiest first (a budget-limited run spends where it matters)
    candidates: list = []
    for name in _resolve_fleet(names, allow_empty=allow_empty, verb="flow audit"):
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001 - a broken spec must not kill the audit
            run.skipped.append((name, f"unreadable spec: {exc}"))
            continue
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        if spec.mutate is not None:
            run.skipped.append((name, "write flow (never captured, never judged)"))
            continue
        if not spec.audit:
            run.skipped.append((name, "audit not enabled"))
            continue
        audit.prune(cache, key)
        arts = audit.load_artifacts(cache, key)
        if not arts:
            continue
        if _load_meta(cache, key).quarantine is not None:
            # already quarantined deterministically — an audit must never overwrite that reason
            run.skipped.append((name, "already quarantined"))
            continue
        for art in arts:
            prio = 0 if (art.get("signals") or art.get("mode") != "replay") else 1
            candidates.append((prio, art.get("ts") or 0, name, spec, key, art))
    candidates.sort(key=lambda c: (c[0], c[1]))

    if run.no_llm or router is None:
        run.unjudged = len(candidates)
        return run

    snap = router.totals.snapshot() if hasattr(router.totals, "snapshot") else None
    for _prio, _ts, name, spec, key, art in candidates:
        if run.calls >= max_calls:                      # checked BEFORE every call — never overshoots
            run.budget_exhausted = True
            run.unjudged += 1
            continue
        finding = await audit.judge(router, art)
        run.calls += 1
        run.judged += 1
        mode = "advisory" if dry_run else (spec.audit or "advisory")
        code = audit.decide(art, finding, mode=mode)
        adv_code = (finding or {}).get("reason_code") if isinstance(finding, dict) else None
        if code:
            sink = QuarantineSink(cache, key, name)
            sink.quarantine(code)
            run.quarantined += 1
            run.findings.append(AuditFinding(name, code, True, art.get("ts") or 0,
                                             (finding or {}).get("evidence_quote") or ""))
        elif adv_code and adv_code in audit.REASONS:
            AdvisorySink(cache, key, name).quarantine(adv_code)
            run.advisories += 1
            run.findings.append(AuditFinding(name, adv_code, False, art.get("ts") or 0,
                                             (finding or {}).get("evidence_quote") or ""))
        elif isinstance(finding, dict) and finding.get("injection_suspected") and art.get("markers"):
            AdvisorySink(cache, key, name).quarantine("semantic_mismatch_other")
            run.advisories += 1
        if not keep:
            audit.drop(art.get("_path"))                # its whole purpose was one judge call
    if snap is not None and hasattr(router.totals, "since"):
        try:
            run.calls = max(run.calls, router.totals.since(snap).calls)
        except Exception:  # noqa: BLE001 - accounting is best-effort, never load-bearing
            pass
    return run


async def run_all(
    *, names: Optional[list[str]] = None, approved_only: bool = True, include_writes: bool = False,
    concurrency: Optional[int] = None, on_drift: str = "raise", provider_name: Optional[str] = None,
    cache: Optional[FlowCache] = None, allow_empty: bool = False,
) -> list[FleetRun]:
    """Replay every saved flow once (concurrently) and return each outcome — the thin fleet
    supervisor behind `ultracua flow run-all`.

    Safe defaults for unattended use: **read flows only** (write flows are skipped unless
    `include_writes=True`) and **approved flows only**. Each replay records its outcome into health
    as usual. Point cron / Task Scheduler at the CLI and alert on a non-zero exit (any flow failed).
    Concurrency is capped (each replay uses its own browser); pass `concurrency=` or set
    `ULTRACUA_CONCURRENCY`.
    """
    cache = cache or _default_cache()
    names = _resolve_fleet(names, allow_empty=allow_empty, verb="flow run-all")
    sem = asyncio.Semaphore(concurrency or settings.concurrency)

    async def _one(name: str) -> FleetRun:
        # ONE guard for the whole flow, not one per read. `cache.get` raises `CacheUnreadableError` rather
        # than flattening a read failure into "not learned", and this body reads the cache in several
        # places — the write gate here, and again inside `replay()`. Guarding them one at a time is how
        # the first version of this was written, and it missed `replay()`'s: `except FlowReplayError`
        # below does NOT catch a RuntimeError, and `gather` has no `return_exceptions`, so ONE unreadable
        # recipe would cancel every OTHER flow's scheduled run on this cron tick. A boundary guard covers
        # every read on the path, including ones added later. Loud for this flow, not for the fleet — and
        # the flow is REFUSED, never run, because an unreadable recipe cannot be shown not to write.
        try:
            return await _one_guarded(name)
        except CacheUnreadableError as exc:
            _log.error("run-all %r: cached recipe unreadable — %s", name, exc)
            return FleetRun(name=name, ok=False, status="failed",
                            error=f"cached recipe unreadable, refusing to run it blind: {exc}")

    async def _one_guarded(name: str) -> FleetRun:
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001 - a missing/malformed spec is a failed flow, not a crash
            return FleetRun(name=name, ok=False, status="failed", error=f"load failed: {exc}")
        key = flow_key(spec.goal, spec.start_url, spec.scope)
        # THE FOURTH TRANSCRIPTION. `is_write_flow` was extracted to stop this predicate existing in
        # triplicate (mcpserver, run_batch, `flow approve --all`) — and `run_all`, the UNATTENDED cron
        # driver, was the copy that got missed. It gated on `spec.mutate is not None` alone, so an
        # UNDECLARED write — `spec.mutate=None` but a cached step `mutating=True` — was never skipped by
        # `include_writes=False`.
        #
        # That population is not hypothetical, and this release is what creates it at scale: the wire
        # promotion in `_author_steps` now marks a formless JS fetch-POST or a method-less `<form onSubmit>`
        # commit as mutating on a flow LEARNED AS A READ. Such a flow then replayed on every scheduled tick
        # with no confirm barrier (`_preflight_row` computes `is_mutate` from `spec.mutate` alone, so no
        # UnkeyedWriteError either), and `_make_finalize`'s navigate-only branch reports `found=True`
        # unconditionally — so the run looked fine whether or not the write landed.
        if is_write_flow(spec, cache.get(key)):
            if spec.mutate is None:
                # An UNDECLARED write is skipped whatever `include_writes` says, exactly as `run_batch`
                # already refuses one: with no `spec.mutate` there is no confirm barrier, so replay cannot
                # tell whether the write landed. `--include-writes` is consent to run writes that can be
                # VERIFIED, not consent to fire unverifiable ones on a schedule.
                return FleetRun(name=name, ok=False, status="skipped",
                                error="UNDECLARED write — a recorded step mutates but the spec declares no "
                                      "write, so replay cannot verify it landed. Review it "
                                      "(`flow inspect`) and declare it (mutate.confirm_*) or re-record. "
                                      "--include-writes does NOT cover this.")
            if not include_writes:
                return FleetRun(name=name, ok=False, status="skipped",
                                error="write flow (use --include-writes)")
        meta = _load_meta(cache, key)
        if approved_only and not meta.approved:
            return FleetRun(name=name, ok=False, status="skipped", error="not approved")
        async with sem:  # only actual replays consume a browser slot; the skips above are free
            t0 = time.perf_counter()
            try:
                data = await replay(spec, require_approved=approved_only, on_drift=on_drift,
                                    provider_name=provider_name, cache=cache)
                return FleetRun(name=name, ok=True, status="ok",
                                ms=(time.perf_counter() - t0) * 1000.0, data=data)
            except FlowReplayError as exc:
                return FleetRun(name=name, ok=False, status="failed",
                                ms=(time.perf_counter() - t0) * 1000.0, error=str(exc))

    # One driver for the whole fan-out. Every flow still gets its own browser (the docstring's
    # "each replay uses its own browser" is unchanged) — this only stops N concurrent sessions from
    # each launching, and then tearing down, their own Node driver.
    async with driver_scope():
        return await asyncio.gather(*[_one(n) for n in names])


# --- row batch driver (H3 slice 2b) -----------------------------------------------------------
@dataclass
class BatchRowResult:
    """One row's outcome in a `run_batch`. Identified by its INPUT INDEX only — the row's values are
    NEVER stored (a value may be a secret; `repr(BatchRun)` is provably plaintext-secret-free)."""

    index: int                       # position in the input `rows` list — the row's identity
    status: str                      # "ok" | "failed" | "skipped" | "invalid" | "planned" | "resumed"
    ok: bool
    ms: float = 0.0
    data: Any = None                 # replay()'s return (read data, or {"status","data"} for a write)
    error: Optional[str] = None
    idempotency_keys: list = field(default_factory=list)  # sha256 write-key PREVIEW (secret-safe)


@dataclass
class BatchRun:
    """The outcome of a `run_batch`: per-row results + a roll-up."""

    status: str                      # "ok" | "failed" | "invalid" | "planned"
    rows: list                       # list[BatchRowResult]
    total: int = 0
    ok_count: int = 0                # rows freshly actuated this run (NOT resume-skipped)
    failed: int = 0
    skipped: int = 0                 # halt-skips (an earlier row failed under on_row_error="stop")
    invalid: int = 0
    resumed: int = 0                 # rows already committed on a prior run (resume) — skipped, not re-fired
    dry_run: bool = False
    job_id: Optional[str] = None     # the resume token this run keyed its ledger on (None = no ledger)


async def run_batch(
    spec: Optional[FlowSpec], rows: list, *, max_rows: Optional[int] = None,
    on_row_error: str = "stop", dry_run: bool = False, require_approved: bool = True,
    check_shape: bool = True, provider_name: Optional[str] = None, cache: Optional[FlowCache] = None,
    resume: Optional[str] = None,
) -> BatchRun:
    """Drive ONE parameterized flow once per row — the H3 slice-2b VOLUME verb ("record once, run for N
    rows"). A ROW-granular sibling of `run_all` (which is flow-granular). Each row goes through the full
    safety-gated `replay(spec, params=row)`, inheriting 2a's row-keyed Idempotency-Key (distinct rows ->
    distinct keys; a retry of one row -> the same key).

    Safety posture:
      - **All-or-nothing pre-flight**: every row is validated 0-LLM (no browser) via `_preflight_row`
        BEFORE any actuation; ANY invalid row -> `status="invalid"`, ALL bad rows reported, ZERO writes.
      - **Duplicate-row refusal (writes)**: two rows that would mint the SAME Idempotency-Key are refused
        pre-flight — a backend dedupe would silently suppress the second (add a disambiguating slot).
      - **Approval bound**: `max_rows` is REQUIRED for a write batch (one approval must not authorize
        unbounded writes) and refuses when exceeded.
      - **Fail-loud isolation**: `on_row_error="stop"` (default) halts on the first failed row and marks the
        rest `skipped`; `"continue"` runs every row and reports each. A non-`FlowReplayError` crash always
        hard-stops (page state is suspect) but is fully recorded.
      - **Dry-run**: `dry_run=True` validates + plans + previews each row's Idempotency-Key and actuates
        NOTHING (no browser, no health) — review the plan before committing writes.
      - **Resume (slice 2c)**: `resume="<job-id>"` keys a durable per-row ledger. A re-run under the SAME
        job-id SKIPS rows that already committed (status `"resumed"`) rather than re-firing their writes — so
        a batch that died at row 300 finishes rows 300.. instead of re-writing 1..299. A fresh/absent id is
        an independent run (recurrence-safe). The Idempotency-Key remains the correctness floor: a row lost
        to a crash-window re-fires with the SAME key and the backend dedupes it (see `ledger.RunLedger`).

    Rows must be secret-free (a secret slot resolves from `$env`, never a row value). The report stores
    only indices + hashed key previews. Sequential, in input order (a deterministic committed prefix — the
    invariant the resume ledger checkpoints)."""
    if not rows:
        # An empty batch did nothing — a clean, honest roll-up. (Also makes the `run_batch(None, [])`
        # capability probe return a valid shape without dereferencing `spec`.)
        return BatchRun(status="ok", rows=[], total=0, dry_run=dry_run)
    if spec is None:
        raise FlowReplayError("run_batch: a non-empty batch needs a spec")
    if on_row_error not in ("stop", "continue"):
        raise FlowReplayError(f"run_batch: on_row_error must be 'stop' or 'continue', not {on_row_error!r}")

    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    cached_flow = cache.get(key)
    if cached_flow is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to batch — learn and approve the flow first")
    # Key the write guards off the ACTUAL mutating signal, not just the declaration. A flow learned as a
    # "read" (spec.mutate=None) whose steps in fact POST is cached with `step.mutating=True` and, on replay,
    # STILL fires the write (flow._replay_step gates on `step.mutating`). Trusting spec.mutate alone would
    # let such a flow skip run_batch's max_rows blast-radius bound AND its duplicate-row (suppressed-write)
    # refusal while the wire writes — so ANY mutating step makes this a write batch.
    is_mutate = is_write_flow(spec, cached_flow)

    # A batched write MUST be a DECLARED write. A flow learned as a "read" (spec.mutate=None) whose steps in
    # fact POST still FIRES the write on replay, but replay does NO write-landed confirm for it (its confirm
    # barrier keys off spec.mutate) — so its writes are unverified fire-and-hope, and the resume ledger below
    # would record a row off a page-controlled status field it can't trust (a false skip = a silently-lost
    # write). Refuse loud: declare the write + a confirm check so each row's landing is verified. (Matches the
    # recorder, which refuses to cache an undeclared write.)
    if is_mutate and spec.mutate is None:
        raise FlowReplayError(
            f"{spec.name!r}: this flow performs a write (a mutating step) but isn't declared as a write — so "
            f"replay can't verify each row's write LANDED, which a batch (and its resume ledger) requires. "
            f"Declare it via `mutate` with a confirm check (e.g. `flow set-mutate`) and re-approve, then batch it.")

    # RESUME LEDGER (slice 2c): only a WRITE batch with an explicit `resume` job-id keys a durable ledger; a
    # re-run under the SAME id skips rows that already committed. A read batch (idempotent) or no id -> no
    # ledger. Validate the id + load the committed set up front (fail fast on a bad/foreign ledger).
    ledger = None
    if resume is not None and is_mutate:
        try:
            ledger = RunLedger.open(cache, key, resume, spec.scope)
            ledger.committed()
        except LedgerError as exc:
            raise FlowReplayError(f"{spec.name!r}: {exc}") from exc

    # Approval bound: a write batch MUST declare its blast radius (one approval now authorizes N writes).
    if is_mutate and max_rows is None:
        raise FlowReplayError(
            f"{spec.name!r}: a write batch requires max_rows — one approval must not authorize unbounded "
            f"writes. Pass max_rows=N (>= the row count) after reviewing the input.")
    if max_rows is not None and len(rows) > max_rows:
        raise FlowReplayError(
            f"{spec.name!r}: batch has {len(rows)} rows but max_rows={max_rows} — refuse. Raise max_rows only "
            f"after reviewing the extra rows.")

    # ALL-OR-NOTHING PRE-FLIGHT (0-LLM, no browser): validate every row + compute its write-key preview.
    resolved_rows: list[Optional[dict]] = []
    preview_keys: list[list] = []
    invalid: list[BatchRowResult] = []
    for i, row in enumerate(rows):
        try:
            resolved = _preflight_row(spec, row, meta=meta, cached_flow=cached_flow,
                                      require_approved=require_approved, on_drift="raise")
        except FlowReplayError as exc:
            invalid.append(BatchRowResult(index=i, status="invalid", ok=False, error=str(exc)))
            resolved_rows.append(None)
            preview_keys.append([])
            continue
        resolved_rows.append(resolved)
        preview_keys.append(_plan_idempotency_keys(spec, resolved, cached_flow) if is_mutate else [])

    # DUPLICATE-ROW REFUSAL (writes only): two rows that mint the SAME key(s) would let a backend dedupe
    # silently suppress the second — a silent suppressed write. Reads are exempt (identical reads are inert).
    if is_mutate:
        seen: dict = {}
        for i, keys in enumerate(preview_keys):
            if resolved_rows[i] is None:
                continue
            kt = tuple(keys)
            if kt in seen:
                invalid.append(BatchRowResult(
                    index=i, status="invalid", ok=False,
                    error=f"duplicate of row {seen[kt]} — an identical write would mint the same "
                          f"Idempotency-Key, so a backend dedupe would silently suppress it. Add a "
                          f"disambiguating slot (e.g. a reference/nonce) if these are distinct writes."))
                resolved_rows[i] = None
            else:
                seen[kt] = i

    if invalid:
        report = sorted(invalid, key=lambda r: r.index)
        return BatchRun(status="invalid", rows=report, total=len(rows), invalid=len(report),
                        dry_run=dry_run, job_id=resume)

    # DRY-RUN: the plan is valid and complete; actuate nothing (no browser, no health). Under `resume`, a row
    # already committed on a prior run previews as "resumed" (it would be skipped) rather than "planned".
    if dry_run:
        report = [BatchRowResult(
            index=i, ok=True, idempotency_keys=preview_keys[i],
            status="resumed" if (ledger and ledger.is_committed(preview_keys[i])) else "planned")
            for i in range(len(rows))]
        resumed = sum(1 for r in report if r.status == "resumed")
        return BatchRun(status="planned", rows=report, total=len(rows), resumed=resumed,
                        dry_run=True, job_id=resume)

    # EXECUTE sequentially, in input order, applying the failure policy.
    report = []
    stopped = False
    # Hold this loop's Playwright driver across the WHOLE batch. Each row still gets its own browser and
    # its own context, so per-row isolation is untouched — but the ~364 ms driver start is paid once for
    # the batch instead of once per row. Without a held reference the count falls to zero between rows and
    # the driver restarts every time; measured, that is the difference between no win at all and ~2.9x on
    # setup. Released in the `finally` below, so the failure policy and any raise still tear it down.
    await _acquire_driver()
    try:
        for i, row in enumerate(rows):
            if stopped:
                report.append(BatchRowResult(index=i, status="skipped", ok=False,
                                             error="skipped — an earlier row failed (on_row_error='stop')"))
                continue
            # RESUME: a row already committed under this job-id is SKIPPED, not re-fired (no browser). It does
            # NOT consume the stop budget — its write landed on a prior run, so it satisfies "committed once".
            if ledger is not None and ledger.is_committed(preview_keys[i]):
                report.append(BatchRowResult(index=i, status="resumed", ok=True,
                                             idempotency_keys=preview_keys[i],
                                             error="already committed on a prior run (resume) — not re-fired"))
                continue
            t0 = time.perf_counter()
            try:
                data = await replay(spec, params=row, require_approved=require_approved, on_drift="raise",
                                    check_shape=check_shape, provider_name=provider_name, cache=cache)
                report.append(BatchRowResult(index=i, status="ok", ok=True,
                                             ms=(time.perf_counter() - t0) * 1000.0, data=data,
                                             idempotency_keys=preview_keys[i]))
                # Record STRICTLY AFTER the write confirmed (durable before the next row fires). A crash
                # before this leaves the row unrecorded -> a re-run re-fires it with the SAME key -> the
                # backend dedupes (never a silent double-write).
                if (ledger is not None and preview_keys[i] and isinstance(data, dict)
                        and data.get("status") in ("confirmed", "already-done")):
                    ledger.record(i, preview_keys[i], data["status"])
            except FlowReplayError as exc:
                # A raise is not automatically "nothing happened". `WriteReadbackError` means the confirm
                # PASSED and only the readback missed — the payment went through. Leaving that row
                # unrecorded made the ONE case we positively know committed the one case the ledger was not
                # armed for, and `_flow_run_batch` then printed "to resume the rows that DIDN'T commit" for
                # a row that did — instructing the operator to fire it again. Recorded on the way past.
                # Only ever for `landed` errors: a MAYBE must stay unrecorded, because `ledger.py`'s
                # invariant is "never a false skip of an un-landed write" and a keyed retry is the safer
                # side of that trade.
                if (getattr(exc, "landed", False) and ledger is not None and preview_keys[i]):
                    ledger.record(i, preview_keys[i], getattr(exc, "code", "landed"))
                report.append(BatchRowResult(index=i, status="failed", ok=False,
                                             ms=(time.perf_counter() - t0) * 1000.0, error=str(exc),
                                             idempotency_keys=preview_keys[i]))
                if on_row_error == "stop":
                    stopped = True
            except Exception as exc:  # noqa: BLE001 - a crash (browser/unexpected) hard-stops (page state is
                #                       suspect — firing more writes into it is the silent-continue danger),
                #                       but is fully recorded, never swallowed.
                report.append(BatchRowResult(index=i, status="failed", ok=False,
                                             ms=(time.perf_counter() - t0) * 1000.0,
                                             error=f"{type(exc).__name__}: {exc}",
                                             idempotency_keys=preview_keys[i]))
                stopped = True
    finally:
        if ledger is not None:
            ledger.close()
        await _release_driver()

    ok_count = sum(1 for r in report if r.status == "ok")
    failed = sum(1 for r in report if r.status == "failed")
    skipped = sum(1 for r in report if r.status == "skipped")
    resumed = sum(1 for r in report if r.status == "resumed")
    return BatchRun(status="ok" if (failed == 0 and skipped == 0) else "failed", rows=report,
                    total=len(rows), ok_count=ok_count, failed=failed, skipped=skipped, resumed=resumed,
                    dry_run=False, job_id=resume)


@dataclass
class CanaryResult:
    """One flow's freshness verdict from a `canary` probe."""

    name: str
    status: str           # "fresh" | "stale" | "not-learned" | "error"
    detail: str = ""


async def canary(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> CanaryResult:
    """A cheap, READ-ONLY staleness probe: does the flow still *start*? Navigate to the start URL (with
    the flow's auth cookies / headers) and check the FIRST cached actionable step's locator still
    resolves — with **no actions, no writes, and no health record**. Catches entry-page rot EARLY (a
    redesigned landing/login page, a moved entry control) so a scheduled flow is flagged the day the site
    changes, not when its 3am run fails. Intentionally shallow — mid-flow drift is still caught by the
    full `run_all` replay; the canary is a fast first-line warning you can run far more often.
    """
    cache = cache or _default_cache()
    flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    if flow is None:
        return CanaryResult(spec.name, "not-learned", "learn the flow first")
    first = next((s for s in flow.steps if s.locator is not None), None)
    if first is None:
        return CanaryResult(spec.name, "fresh", "no locator step to probe")
    try:
        session = await BrowserSession(headless=spec.headless, storage_state=spec.storage_state).start()
    except Exception as exc:  # noqa: BLE001 - a browser/profile problem is ours, not the flow's
        return CanaryResult(spec.name, "error", f"browser start failed: {type(exc).__name__}: {exc}")
    try:
        if spec.headers:
            await session.set_extra_http_headers(spec.headers)
        await session.goto(spec.start_url)
        # unique=True: an entry control that's now ambiguous is as stale as one that's gone — either way a
        # 0-LLM replay can't trust it. resolve does no action, so this never touches the page's state.
        loc = await resolve(session.page, first.locator, unique=True)
        if loc is None:
            return CanaryResult(spec.name, "stale", f"entry control no longer resolves: {first.intent!r}")
        return CanaryResult(spec.name, "fresh")
    except Exception as exc:  # noqa: BLE001 - an unreachable/erroring start page is itself staleness
        return CanaryResult(spec.name, "stale", f"start page not reachable: {type(exc).__name__}: {exc}")
    finally:
        await session.close()


async def canary_all(
    *, names: Optional[list[str]] = None, cache: Optional[FlowCache] = None,
    concurrency: Optional[int] = None, allow_empty: bool = False,
) -> list[CanaryResult]:
    """Probe every saved flow's freshness concurrently — the cheap early-warning counterpart to
    `run_all`. Point cron at `flow canary` more frequently than the full `run-all` to catch rot early."""
    cache = cache or _default_cache()
    names = _resolve_fleet(names, allow_empty=allow_empty, verb="flow canary")
    sem = asyncio.Semaphore(concurrency or settings.concurrency)

    async def _one(name: str) -> CanaryResult:
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001
            return CanaryResult(name, "error", f"load failed: {exc}")
        try:
            async with sem:
                return await canary(spec, cache=cache)
        except CacheUnreadableError as exc:
            # `run_all`'s sibling, and it has the identical `gather`-without-`return_exceptions` blast
            # radius: `canary()` reads the cache, so one unreadable recipe would take down the whole
            # freshness sweep. Reported as its own status, never as "not-learned" — that is the very
            # confusion `CacheUnreadableError` exists to prevent.
            return CanaryResult(name, "error", f"cached recipe unreadable: {exc}")

    # One driver for the whole fan-out. Every flow still gets its own browser (the docstring's
    # "each replay uses its own browser" is unchanged) — this only stops N concurrent sessions from
    # each launching, and then tearing down, their own Node driver.
    async with driver_scope():
        return await asyncio.gather(*[_one(n) for n in names])


# --- recorder (Phase I) -----------------------------------------------------------------------
@dataclass
class RecordResult:
    """Outcome of `record` — a human demonstration captured into a (maybe-cached) flow."""

    spec: FlowSpec
    cached: bool            # True iff the flow was kept (read: verified-by-replay; write: gated + cached)
    reproduced: bool        # did it replay 0-LLM on a fresh session? (read flows only — a write isn't re-run)
    performed_write: bool   # did a write fire on the wire during the demo?
    steps: list[CachedStep]
    is_write: bool = False  # is this a WRITE flow (approval-gated, idempotency-keyed on replay)?
    note: str = ""
    # H3 slice 1b: per-slot audit findings from opt-in `mine_slots` — one entry per mined slot candidate,
    # each {slot, step, value_leak}. `value_leak` is set (a "where" string) for a slot whose demo value
    # echoes into a LATER locator/precondition/URL (a dead template) — the audit refuses to cache such a
    # flow (fail loud). Empty when `mine_slots` is off.
    slot_findings: list = field(default_factory=list)


def _attach_step_confirms(flow, step_confirms: "list[StepConfirm]"):
    """Phase G: bind each `StepConfirm` (in COMMIT ORDER) to the cached flow's mutating steps and return
    `(new_flow, "")` — or `(None, reason)` on a mismatch so the caller fails loud (never caches a half- or
    mis-confirmed multi-write flow). Binding is by ORDINAL among mutating steps (the Nth confirm -> the Nth
    write, in list order), with a strict count check. `expects_intent` (a substring of the bound step's intent
    or accessible name) is REQUIRED when there is >1 write — it anchors each confirm to its write, so a
    mis-ordered or count-padded list (e.g. a benign keyword-classified control inflating the write count)
    fails loud here rather than silently binding a confirm to the wrong write. The binding is FROZEN into the
    cached steps, so replay never re-pairs under later classifier drift; a human still reviews before `approve`."""
    writes = [(i, s) for i, s in enumerate(flow.steps) if s.mutating]
    if len(step_confirms) != len(writes):
        return None, (f"{len(step_confirms)} per-write confirm(s) declared but the flow has {len(writes)} "
                      f"gated write step(s) — they must match 1:1 in commit order")
    multi = len(writes) > 1
    seen_anchors: set = set()
    steps = list(flow.steps)
    for sc, (i, s) in zip(step_confirms, writes):
        if not sc.has_confirm():
            return None, f"a per-write confirm for write step {i} ({s.intent!r}) has no confirm_* check set"
        if multi and not sc.expects_intent:
            return None, (f"a multi-write flow requires expects_intent on every per-write confirm (write "
                          f"step {i}, intent {s.intent!r}) so each confirm is anchored to its write")
        if sc.expects_intent:
            key = sc.expects_intent.lower()
            if multi and key in seen_anchors:
                return None, f"duplicate expects_intent {sc.expects_intent!r} — each must identify ONE write"
            seen_anchors.add(key)
            hay = f"{s.intent} {s.locator.name if s.locator else ''}".lower()
            if key not in hay:
                return None, (f"per-write confirm expects_intent {sc.expects_intent!r} does not match write "
                              f"step {i} (intent {s.intent!r}) — confirms may be out of commit order")
        steps[i] = s.model_copy(update={"confirm": sc})
    return flow.model_copy(update={"steps": steps}), ""


def _slot_base(step: CachedStep) -> str:
    """The PRE-DEDUP slot-name token for a value-bearing step — its field's accessible name sanitized to a
    lower_snake identifier, else a per-action default. `writable_slots` matches an author-supplied name
    against this RAW token (un-deduped): two identically-named fields collide here, so binding surfaces a
    loud ambiguity refusal rather than a silent `amount`/`amount_2` split onto the wrong step."""
    base = (step.locator.name if step.locator is not None else "") or ""
    base = re.sub(r"[^a-z0-9]+", "_", base.strip().lower()).strip("_")
    return base or {"select": "choice"}.get(step.action, "value")


def _slot_name_for(step: CachedStep, taken: set) -> str:
    """A stable, readable slot name for a value-bearing step — the `_slot_base` token, de-duplicated against
    `taken` (the read-side auto-mining path, where every field becomes a distinct slot)."""
    name = base = _slot_base(step)
    n = 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _value_leaks(value: str, later_steps: list) -> Optional[str]:
    """Does the demo `value` echo into a LATER step's locator / precondition / navigate target? If so the
    step would become a dead template (every non-demo value changes that later basis and replay fails), so
    the audit refuses. Returns a short 'where' string, or None. Values under 2 chars are skipped (too short
    to attribute a real echo — avoids dropping a slot on a coincidental '1')."""
    if not value or len(value) < 2:
        return None
    for s in later_steps:
        loc = s.locator
        if loc is not None:
            # Scan the CONTENT-bearing fields resolve() binds on (role+name, text, neighbor anchor, and the
            # Tier-1 testid / placeholder / id) — a demo value echoed into any of these makes a non-demo value
            # fail to resolve. Deliberately NOT `css`: it's a machine-built STRUCTURAL path (tag names +
            # nth-of-type), so a value that's merely a substring of a tag name ("form", "input") would
            # false-positive without ever being a real value-echo (the id part is covered by elem_id).
            for fname, v in (("name", loc.name), ("text", loc.text), ("anchor", loc.anchor),
                             ("testid", loc.testid), ("placeholder", loc.placeholder), ("elem_id", loc.elem_id)):
                if v and value in v:
                    return f"a later {s.action!r} step's locator.{fname}"
        if s.precond_scope and value in s.precond_scope:
            return f"a later {s.action!r} step's precondition"
        if s.action == "navigate" and s.text and value in s.text:
            return f"a later navigate URL"
    return None


def _slotspec_from_domain(domain: Optional[dict]) -> SlotSpec:
    """H3 slice 1c: build a typed SlotSpec from a step's captured site-metadata domain. A <select>'s
    options become a closed `enum`; an input's `pattern` / `max_length` / `required` carry over; a numeric
    range (min/max present) makes it a `number` slot. A `datalist` is a SUGGESTION list (free text is still
    allowed), so it does NOT become a strict enum. No domain -> a plain string slot."""
    d = domain or {}
    if d.get("options") and not d.get("multiple"):
        return SlotSpec(type="string", enum=list(d["options"]))
    # (A <select multiple>'s value is a JSON-ARRAY string, not a single option, so its individual-option
    # list can't be a strict enum — that would reject the flow's own demonstrated value. Keep it a string
    # slot; a very large single <select> similarly captures no options and stays a plain string slot.)
    kw: dict = {"type": "string"}
    if d.get("pattern"):
        kw["pattern"] = d["pattern"]
    if isinstance(d.get("max_length"), int) and d["max_length"] >= 0:
        kw["max_length"] = d["max_length"]
    if d.get("required") is not None:
        kw["required"] = bool(d["required"])
    if d.get("min") not in (None, "") or d.get("max") not in (None, ""):
        try:
            lo = float(d["min"]) if d.get("min") not in (None, "") else None
            hi = float(d["max"]) if d.get("max") not in (None, "") else None
            kw.update(type="number", min=lo, max=hi)
        except (TypeError, ValueError):
            pass  # non-numeric min/max attr -> keep it a string slot
    return SlotSpec(**kw)


def _unbound_secret_steps(flow: "CachedFlow", spec: FlowSpec) -> list:
    """Names of CREDENTIAL fields the demo typed into that are NOT bound to a SECRET slot.

    A secret step reaches replay with `text=""` — the value never touched disk — so replaying it would
    submit an EMPTY credential and the flow would fail somewhere confusing. The supported route is a
    declared secret slot resolved from `$env` per run. Anything else is refused at authoring, loudly, with
    the exact spec to add.

    Covers BOTH ways it can go wrong: no slot at all, and a slot that exists but isn't `secret` — the second
    is reachable on the read path, where `_mine_and_audit_slots` derives slots from captured site metadata
    and so can only ever produce NON-secret ones."""
    declared = spec.slots or {}
    out = []
    for s in flow.steps:
        if not getattr(s, "secret", False):
            continue
        slot = declared.get(s.slot) if s.slot else None
        if slot is None or not getattr(slot, "secret", False):
            out.append((s.locator.name if s.locator is not None else None) or s.intent or "the field")
    return out


def _refuse_unbound_secrets(names: list, spec: FlowSpec) -> str:
    example = names[0] if names else "password"
    slug = "".join(c if c.isalnum() else "_" for c in example).strip("_").lower() or "password"
    return (f"the demo typed into CREDENTIAL field(s) {names!r}. The value was NOT written to disk (the "
            f"recorder blanks a password / one-time-code field at capture), so this recipe would replay an "
            f"EMPTY credential. Declare it instead, so the value resolves from the environment per run and "
            f"is never serialized: slots={{{slug!r}: SlotSpec(type='string', required=True, secret=True, "
            f"secret_env='ULTRACUA_{slug.upper()}')}} and writable_slots=[{slug!r}].")


def _bind_writable_slots(
    flow: "CachedFlow", spec: FlowSpec, names: set,
) -> "tuple[Optional[CachedFlow], dict, list, str]":
    """H3 write-slot binding: bind each author-NAMED write field to its ONE demonstrated `type`/`select`
    step — the EXPLICIT human sign-off that turns a frozen write literal into a parameter (a write field is
    NEVER auto-lifted; mining is read-only). Returns `(marked_flow, mined_slots, findings, reason)`; a
    non-empty `reason` means the caller must refuse to cache (fail loud) and `marked_flow` is None. Reuses
    `_value_leaks` (the value-independence audit) + `_slotspec_from_domain` (site-metadata typing) verbatim."""
    steps = list(flow.steps)
    declared = spec.slots or {}
    by_name: dict = {}                         # RAW (pre-dedup) _slot_base -> [step indices]
    for i, s in enumerate(steps):
        if s.action in ("type", "select"):     # a `press` carries a KEY, never a value
            by_name.setdefault(_slot_base(s), []).append(i)
    # PASS 1 — resolve every named field to EXACTLY ONE step; a structural miss refuses loud (no mis-bind).
    bound: dict = {}
    for name in sorted(names):
        hits = by_name.get(name, [])
        if not hits:
            return None, {}, [], (f"writable_slots names {name!r} but no demonstrated type/select field "
                                  f"derives that slot name (available: {sorted(by_name)}).")
        if len(hits) > 1:
            return None, {}, [], (f"writable_slots name {name!r} is AMBIGUOUS — {len(hits)} demonstrated "
                                  f"fields derive it; give the fields distinct labels so a money field is "
                                  f"never bound to the wrong step.")
        bound[name] = hits[0]
    # PASS 2 — value-independence audit on EVERY bound step; report ALL leaks, refuse if any (a write slot
    # whose demo value echoes into a later locator/precond would retarget the WRONG element — dead + unsafe).
    findings = [{"slot": n, "step": bound[n],
                 "value_leak": _value_leaks(steps[bound[n]].text or "", steps[bound[n] + 1:])}
                for n in sorted(bound)]
    leaks = [f for f in findings if f["value_leak"]]
    if leaks:
        return None, {}, findings, (f"value-independence audit refused writable slot(s) "
                                    f"{[f['slot'] for f in leaks]} — the demo value echoes into "
                                    f"{leaks[0]['value_leak']}, so a non-demo value would target the WRONG "
                                    f"element (a dead AND dangerous write template).")
    # PASS 3 — mark + type. A pre-declared, human-reviewed `spec.slots[name]` WINS (its enum/pattern/range is
    # the tightest contract, and what `approve()`'s slots_hash binds); else mine the type from the field's
    # captured site domain. A secret slot's plaintext demo value is scrubbed from the cache.
    mined: dict = {}
    for name in sorted(bound):
        i = bound[name]
        step = steps[i]
        slot = declared.get(name)
        if slot is None:
            slot = _slotspec_from_domain(step.slot_domain)
            mined[name] = slot
        if slot.secret and not slot.required:
            return None, {}, findings, (f"writable slot {name!r} is secret but not required — a missing "
                                        f"$env would type a blank secret onto the page; mark it required.")
        if step.secret and not slot.secret:
            # A CREDENTIAL field bound to a plain slot. Refuse rather than launder it: the value would
            # become an ordinary tool argument — advertised in the MCP input schema, accepted in `params`,
            # and echoed by `flow inspect` — which is precisely what `secret=True` exists to prevent.
            return None, {}, findings, (f"writable slot {name!r} binds a CREDENTIAL field (the recorder "
                                        f"captured it as a password / one-time-code input) to a "
                                        f"non-secret slot. Declare it "
                                        f"`SlotSpec(secret=True, required=True, secret_env=...)` so the "
                                        f"value resolves from the environment and is never a tool "
                                        f"argument.")
        upd = {"slot": name}
        if slot.secret:
            upd["text"] = ""   # never persist the demo's plaintext secret to the cache (resolved from $env)
        steps[i] = step.model_copy(update=upd)
    return flow.model_copy(update={"steps": steps}), mined, findings, ""


def _mine_and_audit_slots(flow: "CachedFlow", spec: FlowSpec) -> "tuple[CachedFlow, dict, list]":
    """H3 slice 1b/1c: auto-mine each value-bearing step (`type`/`select`) into a typed slot (a `press`
    carries a KEY, never a value), running the value-independence audit as it goes and typing each slot from
    the field's captured site-metadata domain (enum from <select> options, pattern/max_length/range from
    input constraints — slice 1c). Returns `(marked_flow, slots, findings)`; any finding with `value_leak`
    set means a dead template, and the CALLER refuses to cache."""
    steps = list(flow.steps)
    slots: dict = {}
    findings: list = []
    taken: set = set()
    for i, step in enumerate(steps):
        if step.action not in ("type", "select"):
            continue
        name = _slot_name_for(step, taken)
        leak = _value_leaks(step.text or "", steps[i + 1:])
        findings.append({"slot": name, "step": i, "value_leak": leak})
        if leak is not None:
            continue  # dead template — don't mark it; the caller refuses the whole flow
        steps[i] = step.model_copy(update={"slot": name})
        slots[name] = _slotspec_from_domain(step.slot_domain)
    return flow.model_copy(update={"steps": steps}), slots, findings


def _reset_learn_baselines(cache: FlowCache, key: str) -> bool:
    """A successful RE-AUTHORING (learn or record) of an UNAPPROVED flow drops the learn-bound baselines —
    the data shape, the H9 value contracts and the magnitude history all describe the OLD recipe.

    `learn` re-seeds them from its own extraction; `record` has no extracted data, so it CLEARS them and the
    next learn (or the first clean runs, for the magnitude ring) re-establishes them. Symmetry matters: without
    this, a learned-then-recorded flow kept a stale shape forever with no way to re-baseline, which made
    `flow record` a dead end for a legitimate page restructure. An APPROVED flow keeps its blessed baselines
    (a human must `flow unapprove` first) — that is the whole point of the preservation rule.
    Returns True when the baselines were cleared."""
    if _load_meta(cache, key).approved:
        return False
    def _apply(m: FlowMeta) -> None:
        m.shape = None
        m.contracts = None
        m.audit_due = True
    _update_meta(cache, key, _apply)
    _reset_history(cache, key)
    return True


async def record(
    spec: FlowSpec, *, demo: Callable[[Any], Awaitable[None]], headless: bool = False,
    cache: Optional[FlowCache] = None, caption: Optional[Callable[..., Any]] = None,
    provider_name: Optional[str] = None, mine_slots: bool = False,
    writable_slots: Optional[Iterable[str]] = None,
) -> RecordResult:
    """Capture a human DEMONSTRATION of `spec` into a cached, replayable flow (Phase I recorder).

    `demo(page)` drives the demonstration — in the `flow record` CLI it just waits while the human clicks
    through the task in a headed browser; in tests it's a scripted sequence. The capture produces an
    ordinary `CachedFlow`, so the whole replay engine (resolve + drift gate + canary + run-all) works on it.

    **READ flows** verify-by-replay: cached only if their *navigation* reproduces 0-LLM on a fresh session
    (navigation-fidelity, NOT a correctness check — you confirm correctness by watching your own demo).

    **WRITE flows** are captured SAFELY when you DECLARE the write up front via `spec.mutate` (a confirm
    check — the recorder can't infer the action-completion signal). A demonstrated form-submit is recorded
    as a gated mutating step (its `precond_scope` captured inline), and the flow is routed through approval +
    the mutation gate + idempotency exactly like a learned write: it never relearns under drift, the gate
    refuses it under form/section drift (fail loud, no blind re-fire), and replay is approval-gated. A write
    is NOT verify-by-replayed (re-firing it would double-submit) — approval is the human verification.

    If a write is demonstrated WITHOUT a declared confirm check (`spec.mutate` unset) — a non-idempotent
    request / WebSocket frame on the wire, or a keyword-`mutating` step — recording is REFUSED with guidance
    to re-record with `--confirm-*`. **Residual:** a write behind a **GET** link or `navigator.sendBeacon`
    isn't auto-detected; declaring the flow a write (`spec.mutate`) still captures it safely (gate + approval),
    so don't rely on auto-detection for those — declare them. The caller saves the spec so `replay` /
    `run-all` / `canary` find it.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    declared_write = spec.mutate is not None
    # H3 write-slot binding: `writable_slots` is the EXPLICIT sign-off to parameterize named WRITE fields.
    # Refuse the mis-configurations BEFORE any browser opens (config errors should never dial the site):
    # it needs a declared write (a read uses `mine_slots`), and it's mutually exclusive with `mine_slots`
    # (read auto-lift vs write explicit sign-off — pick one).
    ws = {str(n) for n in writable_slots} if writable_slots else None
    if ws is not None:
        if not declared_write:
            return RecordResult(spec, cached=False, reproduced=False, performed_write=False, is_write=False,
                                steps=[], note="writable_slots binds WRITE fields and needs a declared write "
                                               "(spec.mutate / --confirm-*); a read flow uses mine_slots.")
        if mine_slots:
            return RecordResult(spec, cached=False, reproduced=False, performed_write=False, is_write=True,
                                steps=[], note="pass mine_slots (read auto-lift) OR writable_slots (write "
                                               "explicit sign-off), not both.")
    # Intent caption is OPT-IN (an explicit `caption` callable), never auto-wired here: capture itself is
    # key-less, so `record()` must not make a surprise LLM call. The `flow record` CLI builds the real
    # captioner (`caption_for`) and passes it; tests inject a fake. `provider_name` is unused here (kept for
    # signature stability) — the CLI owns captioner construction.
    # A8, at AUTHORING time: probe the declared confirm on the ENTRY page, before the demo touches anything.
    # If it already holds there, it is not unique to this write's outcome and replay would refuse the flow
    # on every run — say so now, while the human is still here.
    #
    # SCOPE, stated because the first version of this comment claimed more than the code delivers: this
    # covers the ENTRY page only. Replay probes its baseline immediately before the FIRST MUTATING STEP,
    # which in a multi-page flow is a different page and a different URL — so a confirm that is absent at
    # entry but already true where the commit happens (a `confirm_url_contains` covering the whole checkout
    # section, a summary region on the review page) passes here and fails at replay, AFTER the write has
    # left. That runtime case is the backstop and raises `WriteUnverifiedError`, which says so in as many
    # words. Probing the true pre-commit page at authoring time needs the recorder to evaluate the confirm
    # at each commit; that is a real slice, not a comment.
    # Reuses `record_demo`'s existing `prepare` hook — the same post-navigation hook replay uses.
    pre_state: dict = {}

    async def _probe_confirm(session) -> None:
        if declared_write and spec.mutate.has_confirm():
            m = spec.mutate
            pre_state["confirm"] = await condition_present(
                session.page, selector=m.confirm_selector, text_contains=m.confirm_text_contains,
                url_contains=m.confirm_url_contains, timeout_ms=0,
            )

    flow, wire_write, crossed_origin, unattributed_writes = await record_demo(
        spec.start_url, demo, goal=spec.goal, cache=cache, scope=spec.scope, headless=headless,
        storage_state=spec.storage_state, extra_headers=spec.headers,  # demo in the SAME context as verify
        mutate=declared_write,  # gate the demonstrated write step(s) at capture time
        caption=caption,        # best-effort intent labels (off the replay path); None -> placeholder intents
        prepare=_probe_confirm,
    )
    detected_write = wire_write or any(s.mutating for s in flow.steps)

    # A CROSS-origin navigation during the demo orphans the prior origin's not-yet-captured events (incl. the
    # navigating click itself) — the recording may be silently truncated, and a write flow isn't verify-by-
    # replayed to catch it. Refuse rather than cache a possibly-incomplete flow. (Same-origin multi-page is
    # fine; cross-origin recording — SSO / external checkout — is a documented unsupported case for now.)
    if crossed_origin:
        cache.delete(key)
        return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                            is_write=declared_write or detected_write, steps=list(flow.steps),
                            note="the demonstration crossed a site/origin boundary (e.g. an SSO or external "
                                 "checkout redirect); steps on the page navigated away from can't be captured "
                                 "reliably, so the flow was NOT cached. Record the cross-origin portion as a "
                                 "separate same-origin flow, or keep the demo on one origin.")

    if not flow.steps:
        cache.delete(key)
        return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write, is_write=False,
                            steps=[], note="no actions were captured — nothing to record.")

    # A write was demonstrated but NOT declared (no confirm check) -> refuse. The recorder can't infer the
    # action-completion signal, so a write must be declared like `flow learn` (spec.mutate / --confirm-*).
    if detected_write and not declared_write:
        cache.delete(key)  # never keep a write flow with no confirm check
        cause = ("a WRITE fired on the wire (a non-idempotent request or a WebSocket frame)" if wire_write
                 else "a WRITE-like (mutating) action was captured")
        return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write, is_write=True,
                            steps=list(flow.steps),
                            note=f"{cause} during the demo — recording a WRITE needs an action-completion "
                                 f"check the recorder can't infer. Re-record declaring the write (a confirm "
                                 f"check: --confirm-text-contains / --confirm-selector / --confirm-url-"
                                 f"contains), or re-record a read-only flow.")

    if declared_write:
        # A DECLARED write: record_demo gated the write step(s) at capture (precond_scope), so the cached
        # flow is routed through approval + the mutation gate + idempotency exactly like a learned write.
        # We do NOT verify-by-replay — re-firing a mutating step would double-submit; a recorded write is
        # verified by the human watching their own demo plus the approval gate, not an automated replay.
        if not spec.mutate.has_confirm():
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                is_write=True, steps=list(flow.steps),
                                note="a write flow needs a confirm check — set mutate.confirm_selector / "
                                     "confirm_text_contains / confirm_url_contains.")
        if pre_state.get("confirm"):
            # The declared confirm was ALREADY TRUE on the entry page, before the demo did anything. It
            # therefore cannot distinguish "this write landed" from "this signal was already there" — which
            # is exactly how a write that never fires reads as confirmed. Refuse now, with the human still
            # watching, rather than cache a recipe every replay would refuse. Same wording as the per-write
            # barrier's, because it is the same mistake one level up.
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                is_write=True, steps=list(flow.steps),
                                note="the declared confirm was already true on the ENTRY page, before the "
                                     "demo — it is not unique to this write's outcome, so it cannot show "
                                     "the write landed. Pick a signal the write itself creates (an order "
                                     "number, a URL the commit navigates to), not a persistent status "
                                     "region or a banner from a previous run.")
        # Fail-closed invariant guard: a recorded write must NEVER be cached UNGATED. Three ways that could
        # slip through, all refused here:
        #   - a mutating step with no precondition (empty precond_scope; the recorder never sets a whole-page
        #     precond_fingerprint, so the replay gate would be a no-op and the step fires blind / under drift);
        #   - `unattributed_writes` > 0: a genuine wire write that could be tied to NO single gated commit —
        #     a DEFERRED write (timer / awaited round-trip / load-or-interval handler), a nested synthetic
        #     commit's turn, or one orphaned by a cross-origin hop (all marker seq=null); OR a WORKER /
        #     cross-realm fetch/xhr write the init-script can't instrument (it surfaces on the wire but emits
        #     no marker — caught by reconciling fetch/xhr requests against fetch/xhr markers). Checked PER WRITE
        #     by COUNT, independent of whether OTHER steps are gated — the masking class the old all-or-nothing
        #     check let through (`wire_write and not gated` is disarmed by any one gated step); or
        #   - a write provably fired ON THE WIRE but NOTHING could be gated at all (belt-and-suspenders).
        # Only a write fired SYNCHRONOUSLY from its own single action is gated; a refusal here means a write
        # couldn't be tied to one action — re-record so each write fires directly from a single action. (A
        # GET-write with NO wire signal and no mutating step is the acknowledged undetectable residual: cached
        # approval-gated — the human-in-the-loop gate is its safety.)
        gated = [s for s in flow.steps if s.mutating and s.precond_scope]
        ungated = [s for s in flow.steps if s.mutating and not s.precond_scope]
        if ungated or unattributed_writes or (wire_write and not gated):
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                is_write=True, steps=list(flow.steps),
                                note="a demonstrated WRITE could not be tied to a single commit (a write fired "
                                     "from a nested/forwarded click, or was deferred past another action, or "
                                     "its precondition wasn't captured) — not cached, to never replay a write "
                                     "ungated. Re-record so each write fires directly from one action.")
        # Phase G: a flow with MORE THAN ONE write but no per-write barriers checks only the whole-flow
        # confirm (the last write); an intermediate write that silently fails wouldn't be caught. Warn loud
        # (and the GUIDE documents declaring `step_confirms`). Not refused, to keep a multi-step single-commit
        # flow (e.g. fill-then-submit) working — only the final commit is a write there.
        n_writes = sum(1 for s in flow.steps if s.mutating)
        if n_writes > 1 and not spec.mutate.step_confirms:
            _log.warning("flow %r: %d write steps but no per-write barriers (mutate.step_confirms) — only the "
                         "whole-flow confirm is checked; an intermediate write failure won't be caught",
                         spec.name, n_writes)
        # UNIFIED write-cache exit. `record_demo` already `cache.put` the BASE flow, so we only re-put when
        # we CHANGE it (attach per-write confirms and/or bind writable slots). `step_confirms` marks mutating
        # commit steps by ordinal; `writable_slots` marks non-mutating type/select fills — disjoint index
        # sets, so both markers coexist on the one cached flow.
        final = flow
        # Attach per-write completion barriers (in commit order). A mismatch refuses — never a half/mis-
        # confirmed multi-write flow.
        if spec.mutate.step_confirms:
            attached, reason = _attach_step_confirms(final, spec.mutate.step_confirms)
            if attached is None:
                cache.delete(key)
                return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                    is_write=True, steps=list(flow.steps),
                                    note=f"per-write confirm checks could not be attached: {reason}.")
            final = attached
        # Bind author-NAMED write fields as parameters (the explicit sign-off). A no-match / ambiguous name /
        # value-echo audit leak refuses to cache (fail loud).
        slot_findings: list = []
        if ws:
            marked, mined, slot_findings, reason = _bind_writable_slots(final, spec, ws)
            if marked is None:
                cache.delete(key)
                return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                    is_write=True, steps=list(final.steps), slot_findings=slot_findings,
                                    note=f"writable_slots binding refused: {reason}")
            final = marked
            spec.slots = {**(spec.slots or {}), **mined} or None
            # A declared slot the author did NOT bind is WARNED (not refused) — the replay binding guard
            # (`_preflight_row`) is the hard stop that refuses a param for an unbound slot.
            for n in set(spec.slots or ()) - ws:
                if not any(s.slot == n for s in final.steps):
                    _log.warning("flow %r: slot %r is declared but not in writable_slots — a param for it is "
                                 "refused at replay; add it to writable_slots to bind it", spec.name, n)
        leaked = _unbound_secret_steps(final, spec)
        if leaked:
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=False, performed_write=wire_write,
                                is_write=True, steps=list(final.steps), slot_findings=slot_findings,
                                note=_refuse_unbound_secrets(leaked, spec))
        if final is not flow:
            cache.put(final)   # re-put ONLY when we changed the base flow (else record_demo's put stands)
        _reset_learn_baselines(cache, key)   # a re-authored UNAPPROVED recipe drops stale baselines
        return RecordResult(spec, cached=True, reproduced=False, performed_write=wire_write, is_write=True,
                            steps=list(final.steps), slot_findings=slot_findings, note="")

    # READ flow: verify-by-replay — only trust a recorded flow that reproduces 0-LLM on a fresh session.
    # (The caller persists the spec — e.g. the `flow record` CLI calls save_spec — so record() stays
    # side-effect-light.)
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=None, cache=cache, mode="replay", headless=True,
        scope=spec.scope, extra_headers=spec.headers, storage_state=spec.storage_state,
    )
    reproduced = report.success
    if not reproduced:
        cache.delete(key)
        return RecordResult(spec, cached=False, reproduced=False, performed_write=False, is_write=False,
                            steps=list(flow.steps),
                            note="the recorded flow did NOT reproduce on a fresh 0-LLM replay — not cached. "
                                 "Re-record (the page may depend on record-time state).")

    # H3 slice 1b (opt-in): auto-mine typed slots + run the value-independence audit. Runs AFTER
    # verify-by-replay (the slot markers are inert for a no-params replay, so the reproduced flow is
    # unchanged). If any mined slot's demo value echoes into a later step, refuse to templatize (fail loud);
    # otherwise mark the steps, publish spec.slots, and re-cache. (Read-side only — a write flow returned above.)
    slot_findings: list = []
    if mine_slots:
        if spec.slots:
            # Never clobber an author-declared typed domain (enum/pattern/range) with bare mined string
            # slots — the two creation paths are mutually exclusive. Fail loud so the caller picks one.
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=True, performed_write=False, is_write=False,
                                steps=list(flow.steps),
                                note="mine_slots won't overwrite an author-declared slot table — drop "
                                     "mine_slots to keep your typed slots, or clear spec.slots to let mining "
                                     "derive them.")
        marked, mined_slots, slot_findings = _mine_and_audit_slots(flow, spec)
        leaks = [f for f in slot_findings if f["value_leak"]]
        if leaks:
            cache.delete(key)
            return RecordResult(spec, cached=False, reproduced=True, performed_write=False, is_write=False,
                                steps=list(flow.steps), slot_findings=slot_findings,
                                note=f"value-independence audit refused to templatize: slot(s) "
                                     f"{[f['slot'] for f in leaks]} — the demo value echoes into "
                                     f"{leaks[0]['value_leak']}, so a non-demo value would break replay (a dead "
                                     f"template). Re-record without that field varying, or drop mine_slots.")
        spec.slots = mined_slots or None
        flow = marked
        cache.put(flow)   # persist the slot markers alongside the verified flow

    # The SIBLING of the write path's refusal above. A read flow can type a credential too (a
    # login-then-read recipe), and `mine_slots` can only ever derive a NON-secret slot from site
    # metadata — so without this, a password field could be laundered into an ordinary parameter.
    leaked = _unbound_secret_steps(flow, spec)
    if leaked:
        cache.delete(key)
        return RecordResult(spec, cached=False, reproduced=True, performed_write=False,
                            is_write=False, steps=list(flow.steps), slot_findings=slot_findings,
                            note=_refuse_unbound_secrets(leaked, spec))
    _reset_learn_baselines(cache, key)   # a re-authored UNAPPROVED recipe drops stale baselines
    return RecordResult(spec, cached=True, reproduced=True, performed_write=False, is_write=False,
                        steps=list(flow.steps), slot_findings=slot_findings)
