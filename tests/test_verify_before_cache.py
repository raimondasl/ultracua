"""Verify-by-replay before cache (+ oracle calibration).

Discovery is stochastic — a flow can look "solved" in the learn session yet not reproduce on a fresh
load (its cached locators leaned on learn-time state). Verify-by-replay re-runs the authored flow
0-LLM on a fresh session and caches it ONLY if every step reproduces.

The "oracle calibration" tests below measure the gate the way the research said to: feed it a known-GOOD
flow (must accept) and a known-BROKEN flow (must reject), so we trust the gate before building best-of-N
on top of it.
"""

from __future__ import annotations

from pathlib import Path

from ultracua import flow as flowmod
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import _verify_by_replay, run_cached
from ultracua.locators import LocatorSpec
from ultracua.providers.scripted import ScriptedProvider
from ultracua.safety import PacingGovernor

from benchmarks.shop_flow import GOAL, SUCCESS_TEXT, index_url

# The demo flow with intents reworded to avoid the `is_mutating` keyword heuristic (the canonical
# STEPS say "submit the search", and "submit" trips the write classifier — a false positive that would
# make the engine skip verify). These benign intents keep it a pure READ flow.
_READ_STEPS = [
    {"action": "type", "role": "textbox", "name": "search", "text": "widget", "intent": "enter the search query"},
    {"action": "click", "role": "button", "name": "search", "intent": "run the search"},
    {"action": "click", "role": "link", "name": "open widget x", "intent": "open the widget detail page"},
    {"action": "click", "role": "button", "name": "add to cart", "intent": "click the cart button"},
    {"action": "done", "intent": "reached the added-to-cart state"},
]


async def _learn_demo(cache, url, steps=None):
    return await run_cached(url, GOAL, ScriptedProvider(list(steps or _READ_STEPS)), cache,
                            mode="learn", headless=True)


# --- oracle calibration: known-good accepted, known-broken rejected ---------------------------
async def test_verify_by_replay_accepts_a_reproducible_flow(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    assert (await _learn_demo(cache, url)).success
    flow = cache.get(flow_key(GOAL, url))
    assert flow is not None and len(flow.steps) == 4
    verified = await _verify_by_replay(
        url, flow_key(GOAL, url), flow, cache, True, None, PacingGovernor(), "default", None, None, None
    )
    assert verified is True   # a genuinely reproducible flow is ACCEPTED


async def test_verify_by_replay_rejects_a_broken_flow(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await _learn_demo(cache, url)
    flow = cache.get(flow_key(GOAL, url))
    # Corrupt a mid-flow step so it can't resolve on a fresh load -> the flow no longer reproduces.
    flow.steps[2].locator = LocatorSpec(role="link", name="this link is gone", tag="a")
    verified = await _verify_by_replay(
        url, flow_key(GOAL, url), flow, cache, True, None, PacingGovernor(), "default", None, None, None
    )
    assert verified is False   # a non-reproducible flow is REJECTED (false-accept guard)


# --- the gate wired into _learn ---------------------------------------------------------------
async def test_learn_caches_when_verify_passes(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    report = await run_cached(url, GOAL, ScriptedProvider(list(_READ_STEPS)), cache, mode="learn",
                              headless=True, verify_replay=True)
    assert report.success and report.extra.get("verify") == "passed"
    assert cache.get(flow_key(GOAL, url)) is not None   # the verified flow was cached


async def test_learn_skips_cache_when_verify_fails(tmp_path: Path, monkeypatch) -> None:
    # Force the oracle to reject: _learn must NOT cache, and must fail loud (success=False).
    async def _reject(*a, **k):
        return False

    monkeypatch.setattr(flowmod, "_verify_by_replay", _reject)
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    report = await run_cached(url, GOAL, ScriptedProvider(list(_READ_STEPS)), cache, mode="learn",
                              headless=True, verify_replay=True)
    assert report.success is False and report.extra.get("verify") == "failed"
    assert cache.get(flow_key(GOAL, url)) is None       # nothing cached -> next run re-learns


async def test_write_flow_skips_verify_replay(tmp_path: Path, monkeypatch) -> None:
    # A flow with a mutating step must NOT be re-replayed to verify (re-firing a write = double-submit):
    # it caches without the verify gate, and the verifier must never be consulted for such a flow.
    called = {"n": 0}
    real = flowmod._verify_by_replay  # capture before patching (avoid self-recursion)

    async def _spy(*a, **k):
        called["n"] += 1
        return await real(*a, **k)

    monkeypatch.setattr(flowmod, "_verify_by_replay", _spy)
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    steps = list(_READ_STEPS)
    steps[3] = {**steps[3], "intent": "submit the purchase order"}  # -> step.mutating is True
    report = await run_cached(url, GOAL, ScriptedProvider(steps), cache, mode="learn",
                              headless=True, verify_replay=True)
    assert report.success
    assert cache.get(flow_key(GOAL, url)) is not None   # cached on the in-session success
    assert "verify" not in report.extra                 # the gate was skipped...
    assert called["n"] == 0                              # ...and the verifier was never called
    assert SUCCESS_TEXT.lower() in report.final_text.lower()  # the write did land in-session
