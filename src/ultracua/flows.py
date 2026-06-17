"""Define a recurring browser task once, then run it.

A `FlowSpec` is a named, reusable task: a start URL, a goal, how to authenticate, and what data
to pull. `learn()` LLM-authors the flow (and returns it for inspection); `replay()` reproduces
it at 0-LLM navigation, returns the extracted data, and **raises on drift** rather than returning
wrong data. This is the product-facing layer over the `run_cached` engine — see ROADMAP.md.
"""

from __future__ import annotations

import json
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
    note: str = ""


class FlowReplayError(RuntimeError):
    """Replay could not reproduce a flow: no cached flow, page drift, or data not found."""


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


async def learn(
    spec: FlowSpec, *, provider_name: Optional[str] = None, provider=None, router=None,
    cache: Optional[FlowCache] = None,
) -> LearnResult:
    """LLM-author the flow once; cache it; return the learned steps + extracted data to inspect.

    `provider` drives the agent and `router` does the extraction; pass them to use a
    pre-configured client, else they're built from `provider_name`.
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
    cached = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    return LearnResult(
        spec=spec, cached=cached is not None, steps=list(cached.steps) if cached else [],
        data=out.get("data"), found=bool(out.get("found")), note=report.note or report.mode,
    )


async def replay(spec: FlowSpec, *, provider_name: Optional[str] = None, router=None, cache: Optional[FlowCache] = None) -> Any:
    """Replay the learned flow at 0-LLM navigation and return the extracted data.

    Raises `FlowReplayError` on any drift (no cached flow / unresolved locator / fingerprint
    mismatch) or when the flow reached its end but the requested data wasn't found — never
    returns wrong data silently.
    """
    if router is None:
        _, router = _router(provider_name or settings.provider)
    cache = cache or _default_cache()
    out: dict = {}
    report = await run_cached(
        url=spec.start_url, goal=spec.goal, provider=None, cache=cache, mode="replay",
        max_steps=spec.max_steps, headless=spec.headless, scope=spec.scope,
        extra_headers=spec.headers, storage_state=spec.storage_state,
        finalize=_make_finalize(spec, router, out),
    )
    if report.mode == "miss":
        raise FlowReplayError(f"{spec.name!r}: no learned flow — run learn first")
    if not report.success:
        raise FlowReplayError(f"{spec.name!r}: replay failed (page drift?): {report.note or report.mode}")
    if spec.extract is not None and not out.get("found"):
        raise FlowReplayError(
            f"{spec.name!r}: replay reached the end but the data wasn't found "
            f"(page changed?): {out.get('error')}"
        )
    return out.get("data")


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
