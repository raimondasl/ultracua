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
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from .locators import LocatorSpec
from .types import ActionType

SCHEMA_VERSION = 4  # v4: reading-order snapshot (changes ref order + fingerprint basis) + AccName names


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


class FlowCache:
    """Directory-backed JSON store, one file per flow.

    `ttl_seconds=None` means no expiry. Entries whose schema_version doesn't match the
    current code, or that have expired, are treated as a miss (and pruned on read).
    """

    def __init__(
        self, root: Optional[Path | str] = None, ttl_seconds: Optional[float] = None
    ) -> None:
        self.root = Path(root) if root else Path(".ultracua/flows")
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Optional[CachedFlow]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            flow = CachedFlow.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            return None  # corrupt entry -> miss
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
        # Atomic write (temp + os.replace) so a concurrent reader never sees a half-written flow.
        p = self._path(flow.key)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(flow.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False
