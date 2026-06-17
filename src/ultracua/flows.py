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

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from .cache import FlowCache, flow_key
from .config import settings
from .extract import extract
from .flow import run_cached
from .providers import build_router, get_provider


@dataclass
class FlowSpec:
    """A named, reusable recurring task."""

    name: str
    start_url: str
    goal: str
    extract: Optional[str] = None          # what data to pull (None = navigate-only flow)
    extract_schema: Optional[dict] = None  # optional JSON schema for the extracted `data`
    headers: Optional[dict] = None         # auth via extra HTTP headers
    storage_state: Optional[str] = None    # auth via a Playwright storage_state JSON (cookies)
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
    note: str = ""


@dataclass
class FlowMeta:
    """Trust metadata for a learned flow (sidecar next to the cached flow)."""

    approved: bool = False
    shape: Any = None
    learned_ts: float = 0.0
    last_ok_ts: float = 0.0


class FlowReplayError(RuntimeError):
    """Replay could not be trusted: no cached flow, page drift, data not found, or shape change."""


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


def _load_meta(cache: FlowCache, key: str) -> FlowMeta:
    p = _meta_path(cache, key)
    if p.exists():
        try:
            return FlowMeta(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return FlowMeta()


def _save_meta(cache: FlowCache, key: str, meta: FlowMeta) -> None:
    Path(cache.root).mkdir(parents=True, exist_ok=True)
    _meta_path(cache, key).write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")


def _default_cache() -> FlowCache:
    return FlowCache()


def _router(provider_name: str):
    provider = get_provider(provider_name)
    return provider, getattr(provider, "router", None) or build_router(provider_name)


def _make_finalize(spec: FlowSpec, router, out: dict):
    async def _finalize(session):
        if spec.extract is None:
            out["found"] = True  # navigate-only flow: reaching the end IS success
            return {"solved": True}
        try:
            await session.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        try:
            text = await session.page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
        ex = await extract(router, spec.extract, text, schema=spec.extract_schema)
        out["data"], out["found"], out["error"] = ex.data, ex.found, ex.error
        return {"solved": ex.found, "data": ex.data}

    return _finalize


# --- learn / approve / replay -----------------------------------------------------------------
async def learn(
    spec: FlowSpec, *, provider_name: Optional[str] = None, provider=None, router=None,
    cache: Optional[FlowCache] = None,
) -> LearnResult:
    """LLM-author the flow once; cache it; record its output shape; return it to inspect.

    A re-learn preserves any existing `approved` flag (you opted into trusting re-learns).
    """
    if provider is None or router is None:
        dp, dr = _router(provider_name or settings.provider)
        provider = provider if provider is not None else dp
        router = router if router is not None else dr
    cache = cache or _default_cache()
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=provider, cache=cache, mode="learn",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out),
    )
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cached = cache.get(key)
    data, found = out.get("data"), bool(out.get("found"))
    meta = _load_meta(cache, key)  # preserve `approved` across re-learns
    if cached is not None:
        meta.shape, meta.learned_ts = _shape_of(data), time.time()
        _save_meta(cache, key, meta)
    return LearnResult(
        spec=spec, cached=cached is not None, steps=list(cached.steps) if cached else [],
        data=data, found=found, approved=meta.approved, shape=_shape_of(data),
        note=report.note or report.mode,
    )


def approve(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    """Mark a learned flow trusted (so `replay(require_approved=True)` will run it)."""
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    if cache.get(key) is None:
        raise FlowReplayError(f"{spec.name!r}: nothing to approve — learn the flow first")
    meta = _load_meta(cache, key)
    meta.approved = True
    _save_meta(cache, key, meta)


def unapprove(spec: FlowSpec, *, cache: Optional[FlowCache] = None) -> None:
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    meta.approved = False
    _save_meta(cache, key, meta)


async def _attempt_replay(spec, router, cache, key, meta, check_shape):
    """One pure 0-LLM replay attempt. Returns (ok, data, reason)."""
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=None, cache=cache, mode="replay",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out),
    )
    if report.mode == "miss":
        return False, None, "no learned flow — run learn first"
    if not report.success:
        return False, None, f"replay failed (page drift?): {report.note or report.mode}"
    if spec.extract is not None and not out.get("found"):
        return False, None, f"data not found on replay (page changed?): {out.get('error')}"
    data = out.get("data")
    if check_shape and meta.shape is not None and not _shape_matches(meta.shape, _shape_of(data)):
        return False, None, f"data shape changed vs the learned flow (expected {meta.shape})"
    return True, data, ""


async def replay(
    spec: FlowSpec, *, require_approved: bool = False, on_drift: str = "raise",
    check_shape: bool = True, provider_name: Optional[str] = None, provider=None, router=None,
    cache: Optional[FlowCache] = None,
) -> Any:
    """Replay the learned flow at 0-LLM navigation and return the extracted data.

    Trust controls for unattended use:
      - `require_approved=True` — refuse to run a flow that hasn't been `approve`d.
      - `check_shape=True` — treat a change in the data's *structure* (vs the learned run) as drift.
      - `on_drift="raise"` (default) — raise `FlowReplayError` on any drift (never return wrong
        data); `on_drift="relearn"` — re-author the flow instead and return the fresh data.
    """
    cache = cache or _default_cache()
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    meta = _load_meta(cache, key)
    if require_approved and not meta.approved:
        raise FlowReplayError(f"{spec.name!r}: flow not approved — learn it, verify it, then approve")

    if on_drift == "relearn":
        if provider is None or router is None:
            dp, dr = _router(provider_name or settings.provider)
            provider = provider if provider is not None else dp
            router = router if router is not None else dr
    elif router is None:
        _, router = _router(provider_name or settings.provider)

    ok, data, reason = await _attempt_replay(spec, router, cache, key, meta, check_shape)
    if ok:
        meta.last_ok_ts = time.time()
        _save_meta(cache, key, meta)
        return data
    if on_drift == "relearn":
        res = await learn(spec, provider=provider, router=router, cache=cache)
        if res.cached and res.found:
            return res.data
        raise FlowReplayError(f"{spec.name!r}: replay drifted ({reason}) and re-learn failed ({res.note})")
    raise FlowReplayError(f"{spec.name!r}: {reason}")


# --- spec persistence (for the `ultracua flow` CLI) -------------------------------------------
def _specs_dir() -> Path:
    return Path(".ultracua") / "specs"


def save_spec(spec: FlowSpec) -> Path:
    d = _specs_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{spec.name}.json"
    p.write_text(json.dumps(asdict(spec), indent=2), encoding="utf-8")
    return p


def load_spec(name: str) -> FlowSpec:
    p = _specs_dir() / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"no saved flow {name!r} (looked in {p})")
    return FlowSpec(**json.loads(p.read_text(encoding="utf-8")))


def list_specs() -> list[str]:
    d = _specs_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
