""""Could not read it" is not "there isn't one" (R3.4, and the redesign of the `cache.get` contract).

`FlowCache.get` returned `Optional[CachedFlow]` and flattened every failure into `None`. That reads as
"not learned" everywhere, and one safety predicate keys off exactly that shape:

    is_write_flow(spec, cache.get(key))   # None -> False -> "this flow does not write"

So a file the OS declined to hand over for a moment became a flow with no writes, and `run_all`'s
`include_writes=False` — the unattended, scheduled, no-human-present driver — ran it. The fault class is
not hypothetical on this platform: the sibling sidecar loader already carries a retry loop written
specifically for Windows AV/indexer sharing violations (WinError 32) against this same directory. The
guard existed one file over and was never applied here.

The redesign makes the two facts distinct in the type: absent -> `None`, unreadable -> raise. A transient
error is retried first, so the common Windows case doesn't become a hard failure. CORRUPT still returns
`None` on purpose — the bytes are present and are not a flow, so it is genuinely unusable and every
caller's miss path is correct.

Raising moves the burden to the callers, which is the point — they must now SAY what to do. The tests
below the divider pin the four that had to say something other than "propagate", and all four are the
same shape: a loop or fan-out over the whole fleet, where one unreadable file must fail ITS flow without
taking down the others, and must never fall through to "this flow has no writes".

The `run_all` guard is at the FUNCTION BOUNDARY rather than on each read, and that is the point of the
last three tests. The first version guarded the write-gate read only, and missed the second read inside
`replay()` — `except FlowReplayError` does not catch a `RuntimeError`, and `gather` has no
`return_exceptions`, so the blast radius the guard was added to prevent survived thirty lines further
down the same function. One guard covering the whole path also covers reads added later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ultracua.cache import (
    SCHEMA_VERSION,
    CachedFlow,
    CachedStep,
    CacheUnreadableError,
    FlowCache,
    flow_key,
)


def _flow(key: str, *, mutating: bool = False) -> CachedFlow:
    return CachedFlow(key=key, goal="g", start_url="http://127.0.0.1:1/", created_ts=1e9, steps=[
        CachedStep(intent="go", action="click", locator=None, mutating=mutating),
    ])


def test_an_unreadable_entry_raises_instead_of_reporting_not_learned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core of it. The file EXISTS and cannot be read; `None` would say the opposite."""
    cache = FlowCache(root=tmp_path / "c")
    cache.put(_flow("k1"))
    real = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == "k1.json":
            raise OSError(32, "The process cannot access the file because it is being used by another "
                              "process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(CacheUnreadableError) as ei:
        cache.get("k1")
    assert "could not be read" in str(ei.value)


def test_a_transient_error_is_retried_and_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows case this is actually FOR. An AV scanner holding the file for a few milliseconds must
    not become a hard failure — otherwise the fix trades a silent fail-open for a noisy fail-closed and
    the scheduled fleet breaks on a healthy machine."""
    cache = FlowCache(root=tmp_path / "c")
    cache.put(_flow("k2"))
    real = Path.read_text
    calls = {"n": 0}

    def _flaky(self, *a, **kw):
        if self.name == "k2.json":
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(32, "sharing violation")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _flaky)
    flow = cache.get("k2")
    assert flow is not None and flow.key == "k2"
    assert calls["n"] == 2, "the first attempt must be retried, not abandoned"


def test_an_absent_entry_is_still_none(tmp_path: Path) -> None:
    """The control that keeps the change narrow: the ordinary pre-learn state is untouched."""
    assert FlowCache(root=tmp_path / "c").get("nope") is None


def test_a_corrupt_entry_is_still_a_miss_not_a_raise(tmp_path: Path) -> None:
    """Deliberately NOT the same fact. The bytes are there and they are not a flow, so the recipe is
    genuinely unusable and every caller's miss path (replay reports `mode == "miss"` and fails loud) is
    the right one. Raising here would break `flow status` on a half-written file it can already explain."""
    cache = FlowCache(root=tmp_path / "c")
    cache.put(_flow("k3"))
    (tmp_path / "c" / "k3.json").write_text("{not json", encoding="utf-8")
    assert cache.get("k3") is None

    # ...and likewise a well-formed entry from an incompatible schema version.
    (tmp_path / "c" / "k3.json").write_text(
        json.dumps({**json.loads(_flow("k3").model_dump_json()), "schema_version": SCHEMA_VERSION + 99}),
        encoding="utf-8")
    assert cache.get("k3") is None


# ==================== the two callers that must not simply propagate ====================


def test_health_reports_unreadable_rather_than_taking_down_the_fleet_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`health()` backs `flow status` and the MCP tools/list loop. It must degrade VISIBLY — a distinct
    status, not `not-learned`, which is the very confusion this whole change removes."""
    from ultracua import flows as flows_mod

    cache = FlowCache(root=tmp_path / "c")
    spec = flows_mod.FlowSpec(name="f", goal="g", start_url="http://127.0.0.1:1/")
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(_flow(key))
    real = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == f"{key}.json":
            raise OSError(32, "sharing violation")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)
    h = flows_mod.health(spec, cache=cache)
    assert h.status == "unreadable", f"expected a distinct status, got {h.status!r}"
    assert h.cached is False and h.approved is False
    assert "unreadable" in (h.last_error or "")


async def test_run_all_refuses_the_unreadable_flow_and_still_runs_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blast radius. `run_all` gathers without `return_exceptions`, so an uncaught raise here would
    cancel every OTHER flow's scheduled run — one bad file silently stopping the whole cron tick. It must
    fail THIS flow, loudly, and it must never fall through to running it: with the recipe unreadable we
    cannot tell whether it writes, which is exactly what the raise exists to say.
    """
    from ultracua import flows as flows_mod

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache = FlowCache(root=tmp_path / "c")
    ran: list[str] = []

    specs = {}
    for name in ("bad", "good"):
        spec = flows_mod.FlowSpec(name=name, goal=f"g-{name}", start_url="http://127.0.0.1:1/")
        specs[name] = spec
        cache.put(_flow(flow_key(spec.goal, spec.start_url, spec.scope)))
    bad_key = flow_key(specs["bad"].goal, specs["bad"].start_url, specs["bad"].scope)

    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])
    for s in specs.values():                             # a REAL approved sidecar, not a stubbed loader:
        flows_mod._save_meta(cache, flow_key(s.goal, s.start_url, s.scope),   # run_all reads provenance
                             flows_mod.FlowMeta(approved=True))              # now, and provenance is a
                                                                             # property of the file.

    async def _fake_replay(spec, **kw):
        ran.append(spec.name)
        return {"ok": True}

    monkeypatch.setattr(flows_mod, "replay", _fake_replay, raising=False)
    real = Path.read_text

    def _boom(self, *a, **kw):
        if self.name == f"{bad_key}.json":
            raise OSError(32, "sharing violation")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)

    runs = await flows_mod.run_all(names=["bad", "good"], cache=cache, include_writes=True)
    by_name = {r.name: r for r in runs}
    assert by_name["bad"].ok is False
    assert "unreadable" in (by_name["bad"].error or "")
    assert "bad" not in ran, "an unreadable recipe must never be RUN — we cannot tell if it writes"
    assert len(runs) == 2, "the other flow's scheduled run must survive"


async def test_run_all_survives_a_read_that_fails_INSIDE_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sibling the first version of this guard missed, and the reason it is now at the boundary.

    `_one` reads the cache twice: the write gate, and again inside `replay()`. Guarding only the first
    left the second escaping — `except FlowReplayError` does not catch a `RuntimeError`, and `gather` has
    no `return_exceptions`, so one unreadable recipe cancelled every OTHER flow's scheduled run. That is
    the same blast radius the guard was added to prevent, thirty lines further down the same function.
    """
    from ultracua import flows as flows_mod

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache = FlowCache(root=tmp_path / "c")
    specs = {}
    for name in ("bad", "good"):
        spec = flows_mod.FlowSpec(name=name, goal=f"g-{name}", start_url="http://127.0.0.1:1/")
        specs[name] = spec
        cache.put(_flow(flow_key(spec.goal, spec.start_url, spec.scope)))

    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])
    for s in specs.values():
        flows_mod._save_meta(cache, flow_key(s.goal, s.start_url, s.scope),
                             flows_mod.FlowMeta(approved=True))

    async def _replay(spec, **kw):
        if spec.name == "bad":                       # the read that lives INSIDE replay()
            raise CacheUnreadableError("sharing violation on the recipe")
        return {"ok": True}

    monkeypatch.setattr(flows_mod, "replay", _replay, raising=False)

    runs = await flows_mod.run_all(names=["bad", "good"], cache=cache, include_writes=True)
    by_name = {r.name: r for r in runs}
    assert len(runs) == 2, "one unreadable recipe must not cancel the whole cron tick"
    assert by_name["bad"].ok is False and "unreadable" in (by_name["bad"].error or "")
    assert by_name["good"].ok is True


async def test_the_canary_sweep_survives_one_unreadable_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`canary_all` is `run_all`'s sibling with the identical `gather` blast radius, and it reads the
    cache too. It must report the bad flow as an ERROR — never as `not-learned`, which is exactly the
    confusion `CacheUnreadableError` exists to prevent."""
    from ultracua import flows as flows_mod

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    cache = FlowCache(root=tmp_path / "c")
    specs = {n: flows_mod.FlowSpec(name=n, goal=f"g-{n}", start_url="http://127.0.0.1:1/")
             for n in ("bad", "good")}
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: specs[n])

    async def _canary(spec, **kw):
        if spec.name == "bad":
            raise CacheUnreadableError("sharing violation on the recipe")
        return flows_mod.CanaryResult(spec.name, "fresh", "ok")

    monkeypatch.setattr(flows_mod, "canary", _canary, raising=False)

    res = await flows_mod.canary_all(names=["bad", "good"], cache=cache)
    by_name = {r.name: r for r in res}
    assert len(res) == 2, "one unreadable recipe must not cancel the freshness sweep"
    assert by_name["bad"].status == "error" and "unreadable" in (by_name["bad"].detail or "")
    assert by_name["good"].status == "fresh"


def test_the_mcp_tool_list_drops_an_unreadable_flow_instead_of_advertising_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`list_flow_tools` reads the cache TWICE — `health()` and then `_is_write_flow` — at two different
    moments, so a transient failure can hit the second alone. If it escapes, one bad file takes down the
    whole tool list; if it were swallowed to None, `_is_write_flow` would answer False and an UNDECLARED
    write would be advertised as a read-only tool. Neither: the flow drops out, loudly logged."""
    from ultracua import flows as flows_mod
    from ultracua.mcpserver import server as srv

    spec = flows_mod.FlowSpec(name="bad", goal="g", start_url="http://127.0.0.1:1/")
    monkeypatch.setattr(flows_mod, "list_specs", lambda: ["bad"])
    monkeypatch.setattr(flows_mod, "load_spec", lambda n: spec)
    monkeypatch.setattr(flows_mod, "health", lambda s, **kw: flows_mod.FlowHealth(
        name="bad", status="healthy", cached=True, approved=True, runs=1, successes=1,
        consecutive_failures=0, last_run_ts=0.0, last_ok_ts=0.0, last_error=None))

    def _boom(self, key):
        raise CacheUnreadableError("sharing violation on the recipe")

    monkeypatch.setattr(FlowCache, "get", _boom)
    tools = srv.list_flow_tools(cache=FlowCache(root=tmp_path / "c"))
    assert tools == [], "an unreadable recipe must not be advertised, and must not raise"
