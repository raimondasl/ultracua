"""Eval framework core: the scenario registry, check results, and capability probes.

Design notes
------------
- A *scenario* is an async function returning a list of `CheckResult`s, registered via the
  `@scenario(...)` decorator with metadata (group, requires-tier, cost estimate, aspirational flag).
- Aspirational probing: horizon scenarios test capabilities that may not exist yet. They must
  NEVER crash the runner — use `probe`/`probe_call`/`import_probe`, which convert "the API isn't
  there" (ImportError / AttributeError / TypeError / NotImplementedError) into a `missing` check.
  `missing` means "capability not built yet" (the aspirational gap); `fail` means "the capability
  is claimed/built but misbehaved" (a regression). The distinction is the whole report.
- requires tiers: "none" (key-less, local fixtures, $0 — the default run), "llm" (needs a real
  provider key, costs real money), "live" (touches a live external website — opt-in, be polite).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

# Exceptions that mean "this capability doesn't exist yet" when probing a maybe-future API.
# TypeError is included deliberately: probing `replay(spec, params={...})` on a version without
# slots raises TypeError("unexpected keyword argument") — for an aspirational eval that IS the
# signal. (A genuine TypeError bug inside an existing API will surface as `missing` instead of
# `error`; acceptable for this suite, called out here so nobody is surprised.)
MISSING_EXC = (ImportError, ModuleNotFoundError, AttributeError, NotImplementedError, TypeError)


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "missing" | "skip" | "error"
    note: str = ""


def ok(name: str, note: str = "") -> CheckResult:
    return CheckResult(name, "pass", note)


def fail(name: str, note: str = "") -> CheckResult:
    return CheckResult(name, "fail", note)


def missing(name: str, note: str = "") -> CheckResult:
    return CheckResult(name, "missing", note)


def skip(name: str, note: str = "") -> CheckResult:
    return CheckResult(name, "skip", note)


def expect(cond: bool, name: str, note_fail: str = "", *, aspirational: bool = False) -> CheckResult:
    """True -> pass. False -> `missing` when the check probes an aspirational capability
    (not-built-yet is the expected state), else `fail` (a built capability misbehaved)."""
    if cond:
        return ok(name)
    return missing(name, note_fail) if aspirational else fail(name, note_fail)


def import_probe(module: str) -> tuple[bool, Any]:
    """(True, module) if `module` imports, else (False, exception). For future modules
    (e.g. ultracua.attest, ultracua.mcpserver) that H-scenarios probe for."""
    try:
        return True, importlib.import_module(module)
    except MISSING_EXC as exc:
        return False, exc


async def probe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[str, Any]:
    """Call a maybe-missing capability (sync or async). Returns (status, value_or_exc):
    'ok' -> value; 'missing' -> the MISSING_EXC that signals the API isn't there;
    'error' -> any other exception (a real failure inside an existing capability)."""
    try:
        out = fn(*args, **kwargs)
        if inspect.isawaitable(out):
            out = await out
        return "ok", out
    except MISSING_EXC as exc:
        return "missing", exc
    except Exception as exc:  # noqa: BLE001 — an eval must record, never crash
        return "error", exc


@dataclass
class Ctx:
    """Per-scenario context: a fresh temp dir + lazy helpers. The runner constructs one per
    scenario and (for requires='llm') snapshots the router's usage totals around the run."""

    tmp: Path
    _router: Any = None

    def cache(self):
        """A fresh FlowCache rooted in this scenario's temp dir (never the repo's .ultracua)."""
        from ultracua.cache import FlowCache

        return FlowCache(root=self.tmp / "flows")

    def router(self):
        """A real multi-provider Router (requires='llm' scenarios only). Lazily built and cached;
        the runner reads .totals around the scenario for measured per-scenario cost."""
        if self._router is None:
            from ultracua.config import settings
            from ultracua.providers import build_router

            self._router = build_router(settings.provider)
        return self._router


ScenarioFn = Callable[[Ctx], Awaitable[list[CheckResult]]]


@dataclass
class Scenario:
    id: str            # dotted, sortable: "core.replay.zero_llm", "h03.slots.replay_params"
    title: str
    group: str         # "core" | "common" | "h01".."h16"
    fn: ScenarioFn
    requires: str = "none"        # "none" | "llm" | "live"
    aspirational: bool = False    # expected to score low today (capability not built yet)
    est_llm_calls: int = 0        # estimated real-LLM calls for one run (0 for key-less)
    est_cost_usd: float = 0.0     # estimated $ for one run (0.0 for key-less)
    tags: tuple = ()
    notes: str = ""


REGISTRY: dict[str, Scenario] = {}


def scenario(
    *, id: str, title: str, group: str, requires: str = "none", aspirational: bool = False,
    est_llm_calls: int = 0, est_cost_usd: float = 0.0, tags: tuple = (), notes: str = "",
) -> Callable[[ScenarioFn], ScenarioFn]:
    """Register an eval scenario. `id` must be unique (duplicate registration is a loud error —
    two modules silently shadowing each other would corrupt the report)."""
    assert requires in ("none", "llm", "live"), f"bad requires: {requires}"
    if requires == "none":
        assert est_llm_calls == 0 and est_cost_usd == 0.0, f"{id}: key-less scenarios cost $0"

    def deco(fn: ScenarioFn) -> ScenarioFn:
        if id in REGISTRY:
            raise RuntimeError(f"duplicate scenario id: {id}")
        REGISTRY[id] = Scenario(
            id=id, title=title, group=group, fn=fn, requires=requires, aspirational=aspirational,
            est_llm_calls=est_llm_calls, est_cost_usd=est_cost_usd, tags=tuple(tags), notes=notes,
        )
        return fn

    return deco


def load_all_scenarios() -> None:
    """Import every module under evals.scenarios so their @scenario decorators register.
    A module that fails to import is a LOUD error — a broken eval file must not silently
    vanish from the report."""
    import pkgutil

    import evals.scenarios as pkg

    for m in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"evals.scenarios.{m.name}")
