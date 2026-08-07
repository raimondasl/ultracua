"""Flow cache — the spine of the 5-10x speedup (PLAN.md component 2 / §4).

A learned flow is an ordered list of `CachedStep`s — each a resilient `LocatorSpec` +
the action + its intent + the page fingerprint at record time. Flows are keyed by
SHA256(normalized goal + normalized url + scope) and persisted as JSON on disk so repeat
runs replay deterministically with no LLM.

Phase 2 adds TTL + schema-version gating on read (stale/incompatible entries become a
miss) and a `mutating` flag per step (set at learn time) so the replay path can refuse to
blind-replay irreversible actions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from .obs import get_logger

_log = get_logger("cache")

from .locators import LocatorSpec
from .types import ActionType

SCHEMA_VERSION = 4  # v4: reading-order snapshot (changes ref order + fingerprint basis) + AccName names


class CacheUnreadableError(RuntimeError):
    """A cached flow EXISTS on disk but could not be read. Distinct from "no cached flow", because callers
    treat the latter as "not learned" — and one safety predicate turns that into "not a write"."""


class StepConfirm(BaseModel):
    """Per-write action-completion check (Phase G multi-write) — the per-step echo of `MutateSpec.confirm_*`.

    Set ONLY on a mutating commit step. On replay the engine verifies the `confirm_*` condition the moment
    that write actuates, BEFORE proceeding to the next step — a sequential commit barrier, so a multi-write
    flow never silently runs past a write whose completion can't be verified. The check is a TRANSITION
    (absent-before -> present-after the write), so a confirm that was already true can't be a false pass.
    `expects_intent` (required when there is >1 write) anchors each confirm to its write by an intent /
    accessible-name substring, so a mis-ordered list fails loud at attach time.

    (Per-write one-shot RESUME — skip an already-landed write on a re-run — is a separate, deferred slice: a
    stateless page probe can't safely attribute prior page-state to a specific write, so until that's designed
    a multi-write flow re-fires its writes on a manual re-run, like a recurring single write, and is never
    auto-retried after auth-refresh.)"""

    # action-completion (ANY-of, like MutateSpec.confirm_*) — at least one is required.
    confirm_selector: Optional[str] = None
    confirm_text_contains: Optional[str] = None
    confirm_url_contains: Optional[str] = None
    timeout_ms: int = 8000
    # Authoring guard: a substring that must appear in the bound step's intent or accessible name. REQUIRED
    # when a flow has >1 write (so each confirm is anchored to its write, not just ordinally placed); optional
    # for a single write. Catches a mis-ordered / mis-counted step_confirms list loud at attach time.
    expects_intent: str = ""

    def has_confirm(self) -> bool:
        return any((self.confirm_selector, self.confirm_text_contains, self.confirm_url_contains))


class CachedStep(BaseModel):
    intent: str
    action: ActionType
    locator: Optional[LocatorSpec] = None  # set for click/type/select (and a recorded press: the focused
    #                                        field); None for scroll/navigate/click_xy/webmcp_call
    text: Optional[str] = None
    coords: Optional[list[int]] = None  # [x, y] for click_xy (vision tier)
    tool: Optional[str] = None  # WebMCP tool name (webmcp_call)
    args: Optional[dict] = None  # WebMCP tool args (webmcp_call)
    precond_fingerprint: str = ""
    # Precise mutation-gate precondition: a fingerprint of the interactables in the target's
    # enclosing form/section (set only for mutating click/type steps). Lets the gate ignore
    # unrelated page churn (banners, badges) that the whole-page precond_fingerprint over-flags.
    # Empty on older flows / non-mutating steps -> the gate falls back to precond_fingerprint.
    precond_scope: str = ""
    mutating: bool = False  # irreversible side effect -> never blind-replay
    # Phase G: per-write completion barrier (set only on a mutating commit step). Defaulted None -> older
    # flows + non-multi-write flows deserialize unchanged (NO schema bump needed).
    confirm: Optional[StepConfirm] = None
    # H3 typed templates: the name of the FlowSpec slot this step's value comes from. When set AND a
    # `params={slot: value}` is passed to replay, the validated `value` is substituted for `text` at the
    # fill/type/select site ONLY (a `press` carries a KEY like "Enter", never a slot value, so it never
    # substitutes and must never be marked — replay's binding guard counts only type/select bindings);
    # otherwise the frozen `text` is used (so a no-params replay is unchanged). Additive, defaulted None ->
    # older flows deserialize unchanged (NO schema bump needed).
    slot: Optional[str] = None
    # H3 slice 1c: the field's LEGAL DOMAIN captured from site metadata at record time (inert unless the
    # step is mined into a slot): a <select>'s option values, an input's pattern/required/max_length/min/
    # max/datalist. Recorder auto-mining turns this into a typed SlotSpec (enum/pattern/range) so pre-flight
    # validation checks against the real domain. Additive, defaulted None (NO schema bump needed).
    slot_domain: Optional[dict] = None
    # The captured field was a CREDENTIAL (input[type=password], or an autocomplete of current-password /
    # new-password / one-time-code). The recorder stores `text=""` for such a step — the value never reaches
    # disk — and this flag is what says the empty string is a REDACTION rather than a demonstrated empty
    # field. `record()` refuses to cache a secret step that isn't bound to a secret slot, and `flow inspect`
    # masks it. Additive + defaulted, so older flows deserialize unchanged (NO schema bump needed) — the
    # same precedent as `slot` and `slot_domain` above.
    secret: bool = False


class CachedFlow(BaseModel):
    key: str
    goal: str
    start_url: str
    steps: list[CachedStep]
    created_ts: float
    schema_version: int = SCHEMA_VERSION


def _norm_url(url: str) -> str:
    p = urlsplit(url)
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", ""))


def _norm_goal(goal: str) -> str:
    return re.sub(r"\s+", " ", goal.strip().lower())


def flow_key(goal: str, url: str, scope: str = "default") -> str:
    basis = "\n".join([_norm_goal(goal), _norm_url(url), scope])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# --- the approval-bound RECIPE digest ---------------------------------------------------------
# `flow_key` above hashes a flow's IDENTITY (goal+url+scope). `steps_hash` hashes its RECIPE — the actual
# steps a human reviewed — so `approve()` can bind to it and replay can refuse a recipe that changed since.
#
# THE INCLUSION RULE (it derives every list below): include a field iff changing it could make a run do
# something DIFFERENT BUT STILL PASSING, or WEAKEN A GUARD while still passing. Exclude iff changing it can
# only produce a loud failure, or it is already bound elsewhere, or it is pure churn with no trust semantics.
# The usual counter-pressure ("don't un-approve on site-derived churn") does NOT apply here: a cached flow's
# JSON is immutable during healthy operation — every `cache.put` site is an authoring event and `get` never
# rewrites — so every CachedStep field is frozen at capture and the rule resolves toward INCLUDE.
#
# ADDITIVE SAFETY (this is the load-bearing part): `CachedStep` has grown additively several times, each with
# a "NO schema bump needed" comment. Digesting `model_dump()` would therefore un-approve EVERY flow in EVERY
# fleet on the next additive release. So the basis is an EXPLICIT, enumerated allow-list, and `_canon` OMITS
# any field still at its declared default — a newly added defaulted field changes no existing digest. A new
# field lands in `_UNHASHED_*` by choice; promoting it later means bumping STEPS_HASH_VERSION, which
# re-approval-gates every fleet on purpose. `tests/test_cache.py` enforces the classification (field census)
# and pins the exact bytes (golden vector).
STEPS_HASH_VERSION = 1

_HASHED_FLOW_FIELDS = ("key", "goal", "start_url")
_UNHASHED_FLOW_FIELDS = ("created_ts", "schema_version")   # churn / already gated by the loader
_NESTED_FLOW_FIELDS = ("steps",)

# `intent` is NOT cosmetic: it is an input to the write dedupe key (`safety.idempotency_key`), it is what
# `expects_intent` binds a per-write confirm barrier against, and it is the only thing a human actually reads
# in `flow inspect` — i.e. literally the recipe they approved.
_HASHED_STEP_FIELDS = ("intent", "action", "text", "coords", "tool", "args",
                       "precond_fingerprint", "precond_scope", "mutating", "slot", "slot_domain",
                       # `secret` is HASHED by the inclusion rule: flipping it off WEAKENS a guard while
                       # still passing — `flow inspect` would print the field unmasked and the empty `text`
                       # would read as a demonstrated empty value rather than a redaction. It defaults to
                       # False, and `_canon` omits defaults, so every existing digest is byte-identical.
                       "secret")
_UNHASHED_STEP_FIELDS: tuple = ()
_NESTED_STEP_FIELDS = ("locator", "confirm")

_HASHED_LOCATOR_FIELDS = ("role", "name", "tag", "elem_id", "testid", "placeholder",
                          "text", "css", "anchor", "anchor_source",
                          "anchor_id")                                         # ALL of LocatorSpec
_HASHED_CONFIRM_FIELDS = ("confirm_selector", "confirm_text_contains", "confirm_url_contains",
                          "timeout_ms", "expects_intent")                      # ALL of StepConfirm


def _canon(m, fields: tuple) -> dict:
    """Canonical dict over `fields`, OMITTING any whose value still equals its declared default — so adding a
    new defaulted field to a model leaves every existing digest byte-identical."""
    out = {}
    mf = type(m).model_fields
    for f in fields:
        v = getattr(m, f)
        if f in mf and v == mf[f].default:
            continue
        out[f] = v
    return out


def steps_hash(flow: "CachedFlow") -> str:
    """A stable digest of the RECIPE a human reviewed: each step's action, intent, locator, typed text,
    `mutating` flag, commit barrier and slot binding, IN ORDER, plus the flow's identity.

    Honest scope: this is an INTEGRITY check against the system re-authoring itself under a stale approval
    bit — a heal, a suffix-replan, a re-record, a re-learn, or a hand-edited cache file. It is NOT
    tamper-proofing: it is unkeyed and lives in `<key>.meta.json` right beside the `<key>.json` it
    authenticates, so anyone who can rewrite the steps can rewrite the hash. Signing is a separate concern.
    16 hex matches `flow_key` / the slot + contract hashes; the threat model is accident, not preimage.
    """
    rows = []
    for s in flow.steps:
        row = _canon(s, _HASHED_STEP_FIELDS)
        if s.locator is not None:
            row["locator"] = _canon(s.locator, _HASHED_LOCATOR_FIELDS)
        if s.confirm is not None:
            row["confirm"] = _canon(s.confirm, _HASHED_CONFIRM_FIELDS)
        rows.append(row)
    basis = {"v": f"ultracua-steps-v{STEPS_HASH_VERSION}",
             **{f: getattr(flow, f) for f in _HASHED_FLOW_FIELDS},
             "steps": rows}
    blob = json.dumps(basis, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class FlowCache:
    """Directory-backed JSON store, one file per flow.

    `ttl_seconds=None` means no expiry. Entries whose schema_version doesn't match the
    current code, or that have expired, are treated as a miss (and pruned on read).
    """

    def __init__(
        self, root: Optional[Path | str] = None, ttl_seconds: Optional[float] = None
    ) -> None:
        # An explicit `root` always wins (tests, the daemon, a caller with its own store). Otherwise resolve
        # `<flow_home>/flows` — the SAME home `flows._specs_dir()` uses, so the specs dir and the cache can
        # never point at different stores (which is how a wrong-cwd run silently found zero flows).
        if root:
            self.root = Path(root)
        else:
            from .config import flow_home

            self.root = flow_home() / "flows"
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Optional[CachedFlow]:
        """The cached flow, or None if there ISN'T ONE. Raises `CacheUnreadableError` if there is one and
        we could not read it — those are different facts and collapsing them is a fail-OPEN.

        `None` is read by callers as "not learned", and at least one safety predicate keys off exactly
        that: `is_write_flow(spec, cache.get(key))` returns False for None, so "the file could not be read
        right now" became "this is not a write" and an undeclared write ran on the scheduled fleet. The
        fault class is real and documented on the sibling sidecar — the meta loader carries a retry loop
        specifically for Windows AV/indexer sharing violations (WinError 32) on this same directory.

        So a transient OSError is RETRIED, and a persistent one raises rather than being flattened into a
        miss. A CORRUPT entry still returns None: the bytes are there and they are not a flow, so it is
        genuinely unusable and every caller's "no cached flow" path is the right one (replay reports
        `mode == "miss"` and fails loud). Callers that must survive one bad flow — `health()` and the MCP
        `tools/list` loop — catch the error and degrade VISIBLY instead of silently reporting not-learned.
        """
        p = self._path(key)
        raw, io_error = None, None
        for attempt in range(3):
            try:
                if not p.exists():
                    return None                      # genuinely absent — the ordinary pre-learn state
                raw = p.read_text(encoding="utf-8")
                io_error = None
                break
            except OSError as exc:
                io_error = exc
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
        if io_error is not None:
            raise CacheUnreadableError(
                f"the cached flow at {p} exists but could not be read after 3 attempts ({io_error}). "
                f"Refusing rather than treating it as 'not learned' — that reading would let an "
                f"undeclared write through a surface that skips writes.") from io_error
        try:
            flow = CachedFlow.model_validate_json(raw)
        except Exception:
            return None  # corrupt entry -> miss (the bytes are there and are not a flow; fail-loud downstream)
        if flow.schema_version != SCHEMA_VERSION:
            return None  # incompatible -> miss (re-learn)
        if self.ttl_seconds is not None and (time.time() - flow.created_ts) > self.ttl_seconds:
            try:
                p.unlink()  # prune expired
            except OSError:
                pass
            return None
        return flow

    def put(self, flow: CachedFlow) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # Atomic (temp + os.replace) so a concurrent reader never sees a half-written flow, and DURABLE
        # (fsync before the rename) so a host crash / power loss can't leave a zero-length or NUL-filled
        # entry behind — `get()` reads that as a miss, i.e. a silently forgotten recipe. Same reasoning and
        # the same shape as `_save_meta` and the ledger.
        p = self._path(flow.key)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(flow.model_dump_json(indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False

    # --- refusal memory (R3.13) ---------------------------------------------------------------
    #
    # WHY THIS LIVES HERE and not beside `FlowMeta.quarantine`, which is where the register's fix shape
    # put it. The refusal it remembers is raised in the ENGINE (`flow._learn`), and the engine cannot see
    # `FlowMeta`: `flow.py` does not import `flows.py`, and `flows.py` imports `flow.py`, so reaching back
    # is circular. That leaves two options, and only one of them is safe.
    #
    # The rejected one is injecting the memory as a caller-supplied policy, the way `finalize` and
    # `pre_write` are injected. It reads idiomatic and it is a trap: `ultracua run` (cli.py) and the
    # daemon call `run_cached` DIRECTLY, so an opt-in memory protects the `flow` verbs and leaves those
    # two re-authoring — and re-firing the write — on every invocation. That is "a guard in the wrapper
    # rather than the mechanism", which `flow.py`'s own comment calls this codebase's most-repeated defect
    # shape, in a comment written when this very refusal was moved into the engine for that reason. The
    # memory has to be ON BY DEFAULT for a bare `run_cached`, so it belongs on the object the engine
    # already holds.
    #
    # It is deliberately a NARROW, separate concept from `FlowMeta.quarantine` rather than a second
    # writer of it: quarantine answers "may this flow RUN", enforced at `_preflight_row`; this answers
    # "may this flow be RE-AUTHORED", enforced before `_learn`. `flows.release()` clears both, so the
    # operator still sees one story.
    def _refusal_path(self, key: str) -> Path:
        return self.root / f"{key}.refused.json"

    def refusal(self, key: str) -> Optional[dict]:
        """The persisted refusal for this key, or None if there isn't one.

        FAILS CLOSED, and that is the whole difference from `get()`. If the marker exists and cannot be
        read, this returns a synthetic refusal rather than None: "I could not tell whether this flow was
        refused" must not become "it wasn't", because the cost of that wrong answer is re-authoring a
        flow that fires an un-keyed, ungated write. `get()` can afford to raise and let the caller
        decide; this is consulted on the path whose only job is to stop a write, so the conservative
        answer is the refusal itself. Same lesson as the meta loader's provenance (R3.8), pointed the
        other way because the harm is asymmetric here.
        """
        p = self._refusal_path(key)
        try:
            if not p.exists():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return {"code": "refusal_unreadable", "ts": 0.0,
                    "reason": f"a refusal marker exists at {p} and could not be read ({exc}); refusing to "
                              f"re-author rather than assume it said nothing. Delete it by hand if the "
                              f"flow is known good, then re-learn."}

    def remember_refusal(self, key: str, code: str, reason: str) -> None:
        """Persist a refusal so the NEXT invocation refuses without driving a browser.

        Best-effort by construction: this runs immediately after a refusal that has already happened, and
        raising here would convert a correct refusal into an unhandled error on every caller. A failure
        to persist costs the amnesia this exists to remove — the pre-0.81.0 behaviour — so it is logged
        and swallowed, never allowed to mask the refusal it is recording.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            p = self._refusal_path(key)
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"code": code, "reason": reason, "ts": time.time()}, indent=2))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except OSError as exc:
            _log.error("could not persist the refusal for %s (%s) — this run still refuses, but the next "
                       "invocation will re-author and re-fire the write", key, exc)

    def forget_refusal(self, key: str) -> bool:
        """Clear the refusal — the human act. Returns True if there was one."""
        p = self._refusal_path(key)
        try:
            if p.exists():
                p.unlink()
                return True
        except OSError as exc:
            _log.error("could not clear the refusal marker at %s (%s)", p, exc)
        return False
