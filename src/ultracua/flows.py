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

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, StepConfirm, flow_key
from .conditions import condition_present
from .config import settings
from .extract import extract
from .flow import run_cached
from .ledger import LedgerError, RunLedger
from .locators import resolve
from .obs import get_logger
from .pin import find_pin, read_pin
from .providers import build_router, get_provider
from .recorder import caption_intents, record_demo
from .safety import idempotency_key

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
    in `params`, logged, or serialized (mirrors LoginSpec's env-only credential rule)."""

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


@dataclass
class FlowHealth:
    """A flow's status for the fleet view."""

    name: str
    status: str  # not-learned | never-run | healthy | failing | stale
    cached: bool
    approved: bool
    runs: int
    successes: int
    consecutive_failures: int
    last_run_ts: float
    last_ok_ts: float
    last_error: Optional[str]


class FlowReplayError(RuntimeError):
    """Replay could not be trusted: no cached flow, page drift, data not found, or shape change.

    Base of a small TYPED taxonomy so a caller (esp. the H2 MCP server) can react to a failure by
    KIND without string-parsing the message: `.code` is a stable machine-readable slug and
    `.retryable` says whether re-running as-is could plausibly succeed. Every subclass still IS a
    `FlowReplayError`, so existing `except FlowReplayError` keeps catching all of them (the change is
    additive — the base is still raised for config refusals like not-approved / no-confirm-check)."""

    code = "replay_error"
    retryable = False


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


def _classify_replay_failure(kind: str) -> type[FlowReplayError]:
    """Map an `_attempt_replay` failure `kind` to its taxonomy class (default: DriftError)."""
    return {
        "shape": ShapeDriftError,
        "escalate": EscalateError,
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


def _load_meta(cache: FlowCache, key: str) -> FlowMeta:
    p = _meta_path(cache, key)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt/torn file: treat as no meta
            return FlowMeta()
        if isinstance(raw, dict):
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
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    # Atomic write (temp + os.replace) so a crash or a concurrent reader never sees a torn file.
    p = _meta_path(cache, key)
    tmp = f"{p}.{os.getpid()}.tmp"
    Path(tmp).write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
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


def _make_finalize(spec: FlowSpec, router, out: dict, pin: Optional[dict] = None):
    async def _finalize(session):
        if spec.mutate is not None:
            # WRITE flow: success is action-completion — the declared confirm check must hold,
            # else the write didn't land and replay fails loud (Phase D).
            m = spec.mutate
            try:
                await session.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            confirmed = await condition_present(
                session.page, selector=m.confirm_selector,
                text_contains=m.confirm_text_contains, url_contains=m.confirm_url_contains,
                timeout_ms=m.timeout_ms,
            )
            data = None
            if spec.extract is not None:  # optionally also pull a confirmation number, etc.
                try:
                    text = await session.page.inner_text("body")
                except Exception:  # noqa: BLE001
                    text = ""
                ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
                data = ex.data
            out["data"], out["found"] = data, confirmed
            out["error"] = None if confirmed else "write not confirmed (no completion signal on the page)"
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
        try:
            text = await session.page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
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
        finalize=_make_finalize(spec, router, out), verify_replay=verify_replay,
    )
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key)
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
        def _apply(meta: FlowMeta) -> None:  # under the lock: preserve `approved`, refresh shape/pin
            nonlocal pinned, approved
            meta.shape, meta.learned_ts = _shape_of(data), time.time()
            # Bind the pin to the just-learned DOM: a re-learn ALWAYS resets it (a fresh pin, or None
            # when pin_read is off / unpinnable) so a stale pin can never outlive the cached flow.
            meta.read_pin = out.get("pin") if spec.pin_read else None
            pinned = meta.read_pin is not None
            approved = meta.approved

        _update_meta(cache, key, _apply)
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


def approve(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    """Mark a learned flow trusted (so `replay(require_approved=True)` will run it). For a slotted flow,
    the approval is BOUND to the current slot schema — a later domain change refuses replay until
    re-approved (see `replay`)."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    if cache.get(key) is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to approve — learn the flow first")
    sh = _slots_hash(spec)

    def _apply(m: FlowMeta) -> None:
        m.approved = True
        m.slots_hash = sh

    _update_meta(cache, key, _apply)


def unapprove(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    _update_meta(cache, key, lambda m: setattr(m, "approved", False))


def health(spec: FlowSpec, *, cache: Optional[FlowCache] = None, stale_after: Optional[float] = None) -> FlowHealth:
    """A flow's status for the fleet view: not-learned / never-run / healthy / failing / stale.

    `stale_after` (seconds): a flow whose last success is older than this counts as `stale`.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key) is not None
    meta = _load_meta(cache, key)
    if not cached:
        status = "not-learned"
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
        finalize=_make_finalize(spec, router, out, pin=pin),
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
        return False, None, f"data not found / write not confirmed on replay: {out.get('error')}", "drift"
    data = out.get("data")
    if check_shape and meta.shape is not None and not _shape_matches(meta.shape, _shape_of(data)):
        return False, None, f"data shape changed vs the learned flow (expected {meta.shape})", "shape"
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
) -> dict:
    """The 0-LLM, NO-BROWSER trust gate shared by `replay()` and `run_batch()`: resolve + validate one
    row's params against the slot schema and run every guard with ZERO side effects, returning the
    RESOLVED substitution dict (caller params + env-resolved secrets). Raises `FlowReplayError` on any
    violation. Single source of truth — a batch validates every row through this BEFORE actuating any, and
    each actuation re-runs it inside `replay()`, so a guard change lands once and both paths inherit it.

    Guards, in order: 0-LLM pre-flight (`validate_params`); a write needs a confirm check; `on_drift=
    'relearn'` is refused for a write and for any parameterized replay (a re-author drops params → frozen
    defaults); the approval gate; the slot-schema approval gate (a domain widened since approve() must
    re-approve); the binding guard (a supplied slot must bind a type/select step, else its value folds into
    the idempotency key without being typed); the precheck refusal (a parameterized write can't lean on a
    row-blind one-shot precheck)."""
    is_mutate = spec.mutate is not None
    parameterizing = params is not None
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
    # A relearn re-authors the flow from scratch, which does NOT carry `params` — the re-authored flow has
    # no slot-bound steps, so it would run the DEFAULT values and return data for them, silently ignoring the
    # per-run params (a silently-wrong read, inviolable #2). Refuse the combination rather than mislead.
    if parameterizing and on_drift == "relearn":
        raise FlowReplayError(
            f"{spec.name!r}: on_drift='relearn' can't be combined with params — a re-author ignores the "
            f"per-run values and would return data for the frozen defaults. Use on_drift='fail' (the "
            f"default) with params, or drop params to relearn the frozen flow.")
    # A write defaults to approval-gated even without require_approved (stronger trust for writes).
    if (require_approved or is_mutate) and not meta.approved:
        raise FlowReplayError(f"{spec.name!r}: flow not approved — learn it, verify it, then approve")
    # The approval is bound to the slot schema. A slotted flow whose domain changed since approve() (e.g. a
    # payee enum loosened to any string) must refuse until re-approved — a stale approval must never
    # authorize a WIDER contract than the human reviewed (an injection surface, worst on a write).
    if meta.approved and spec.slots and _slots_hash(spec) != meta.slots_hash:
        raise FlowReplayError(
            f"{spec.name!r}: the slot schema changed since approval — re-approve the flow before replaying "
            f"it (a widened/edited slot domain must not run under a stale approval)")
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

    try:
        ok, data, reason, kind = await _attempt_replay(spec, router, cache, key, meta, check_shape,
                                                        params=params)
        if ok:
            return _ok(data)
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
def _specs_dir() -> Path:
    return Path(".ultracua") / "specs"


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


async def run_all(
    *, names: Optional[list[str]] = None, approved_only: bool = True, include_writes: bool = False,
    concurrency: Optional[int] = None, on_drift: str = "raise", provider_name: Optional[str] = None,
    cache: Optional[FlowCache] = None,
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
    names = names if names is not None else list_specs()
    sem = asyncio.Semaphore(concurrency or settings.concurrency)

    async def _one(name: str) -> FleetRun:
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001 - a missing/malformed spec is a failed flow, not a crash
            return FleetRun(name=name, ok=False, status="failed", error=f"load failed: {exc}")
        if spec.mutate is not None and not include_writes:
            return FleetRun(name=name, ok=False, status="skipped", error="write flow (use --include-writes)")
        meta = _load_meta(cache, flow_key(spec.goal, spec.start_url, spec.scope))
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
    is_mutate = spec.mutate is not None or any(s.mutating for s in cached_flow.steps)

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
    concurrency: Optional[int] = None,
) -> list[CanaryResult]:
    """Probe every saved flow's freshness concurrently — the cheap early-warning counterpart to
    `run_all`. Point cron at `flow canary` more frequently than the full `run-all` to catch rot early."""
    cache = cache or _default_cache()
    names = names if names is not None else list_specs()
    sem = asyncio.Semaphore(concurrency or settings.concurrency)

    async def _one(name: str) -> CanaryResult:
        try:
            spec = load_spec(name)
        except Exception as exc:  # noqa: BLE001
            return CanaryResult(name, "error", f"load failed: {exc}")
        async with sem:
            return await canary(spec, cache=cache)

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
    flow, wire_write, crossed_origin, unattributed_writes = await record_demo(
        spec.start_url, demo, goal=spec.goal, cache=cache, scope=spec.scope, headless=headless,
        storage_state=spec.storage_state, extra_headers=spec.headers,  # demo in the SAME context as verify
        mutate=declared_write,  # gate the demonstrated write step(s) at capture time
        caption=caption,        # best-effort intent labels (off the replay path); None -> placeholder intents
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
        if final is not flow:
            cache.put(final)   # re-put ONLY when we changed the base flow (else record_demo's put stands)
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

    return RecordResult(spec, cached=True, reproduced=True, performed_write=False, is_write=False,
                        steps=list(flow.steps), slot_findings=slot_findings)
