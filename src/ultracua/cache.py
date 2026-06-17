"""Flow cache — the spine of the 5-10x speedup (PLAN.md component 2 / §4).

A learned flow is an ordered list of `CachedStep`s — each a resilient `LocatorSpec` +
the action + its intent + the page fingerprint at record time. Flows are keyed by
SHA256(normalized goal + normalized url + scope) and persisted as JSON on disk so repeat
runs replay deterministically with no LLM.

Phase 1 keys the *flow* by goal+url and stores a per-step `precond_fingerprint`; the
research's per-action DOM-hash-in-key refinement and TTL/versioned eviction land in
Phase 2.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from .locators import LocatorSpec
from .types import ActionType

SCHEMA_VERSION = 1


class CachedStep(BaseModel):
    intent: str
    action: ActionType
    locator: Optional[LocatorSpec] = None  # None for press/scroll/navigate
    text: Optional[str] = None
    precond_fingerprint: str = ""


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
    """Directory-backed JSON store, one file per flow."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root) if root else Path(".ultracua/flows")

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Optional[CachedFlow]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return CachedFlow.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            return None  # corrupt/incompatible entry -> treat as miss

    def put(self, flow: CachedFlow) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(flow.key).write_text(
            flow.model_dump_json(indent=2), encoding="utf-8"
        )

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False
