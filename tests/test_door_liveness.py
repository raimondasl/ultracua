"""reshape-plan step 1.7 — the LIVENESS half of the door policy.

`tests/test_door_policy.py` says what each door PERMITS. Without this file that table is
satisfied by a codebase where nothing works: every door could refuse everything and every cell
there would still hold. This drives a read flow green through each class of door.

SEPARATE FILE because these launch a browser and the policy cells do not. The `red-proof` CI job
installs no Playwright, so a killer-suite leg containing a browser cell fails EVERY mutant's
baseline run — measured on CI, and `test_prove_reds_killer_suite_is_browser_free` is what keeps
it that way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ultracua.daemon.server as daemon_mod
import ultracua.flow as flow_mod
import ultracua.flows as flows_mod
import ultracua.parallel as parallel_mod
from ultracua.cache import FlowCache, flow_key
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url

# 5. THE LIVENESS CORPUS.#
# Without this the table is satisfied by a codebase where nothing works: every door could refuse
# everything and each cell above would still hold. This is the other half.

def _mock_router():
    """A key-less Router, because `learn()` only skips building a REAL provider when BOTH a
    provider and a router are supplied — otherwise this test drives live API calls locally and
    raises on CI, which is CLAUDE.md's measured S8/0.84.0 failure."""
    from ultracua.llm.base import Router, Tier
    from ultracua.llm.mock import MockClient
    mc = MockClient(actions=[{"found": True, "data": None}], tool_name="submit")
    return Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))


async def _seed(cache: FlowCache) -> str:
    url = index_url()
    learn = await flow_mod.run_cached(url, GOAL, ScriptedProvider(list(STEPS)), cache,
                                      mode="learn", headless=True)
    assert learn.success, learn.note
    assert cache.get(flow_key(GOAL, url)) is not None
    return url


async def test_a_read_flow_replays_green_through_every_raw_door(tmp_path: Path) -> None:
    """The three un-gated doors, driven for real. They are the ones the table marks as able to
    re-author, so they are the ones whose ordinary behaviour most needs to be shown intact."""
    cache = FlowCache(root=tmp_path)
    url = await _seed(cache)
    ran: dict = {}

    ran["daemon_run"] = await daemon_mod._dispatch("run", {
        "url": url, "goal": GOAL, "mode": "replay", "cache_root": str(tmp_path), "headless": True})
    assert ran["daemon_run"]["success"] and ran["daemon_run"]["llm_calls"] == 0

    reports = await parallel_mod.run_many(
        [{"url": url, "goal": GOAL, "mode": "replay"}], cache=cache, headless=True)
    ran["run_many"] = reports[0]
    assert ran["run_many"].success and ran["run_many"].llm_calls == 0

    ran["cli_root"] = await flow_mod.run_cached(url, GOAL, None, cache=cache, mode="replay",
                                                scope="default", on_step=None)
    assert ran["cli_root"].success and ran["cli_root"].llm_calls == 0

    for door, rep in ran.items():
        text = rep["final_text"] if isinstance(rep, dict) else rep.final_text
        assert SUCCESS_TEXT.lower() in (text or "").lower(), f"{door} did not reach the end state"
    print(f"{len(ran)} raw door(s) replayed a read flow green, 0-LLM: {sorted(ran)}")


async def test_a_read_flow_replays_green_through_the_gated_door(tmp_path: Path) -> None:
    """And the gated one, so the corpus spans both halves of the table's `flow_gates` column."""
    cache = FlowCache(root=tmp_path)
    spec = flows_mod.FlowSpec(name="shop", start_url=index_url(), goal=GOAL)
    # Seeded through the GATED authoring door, not `run_cached` — a `FlowSpec`'s scope is
    # `flow:<name>`, so a flow learned at the raw door lands under a different cache key entirely
    # and `approve` would report nothing to approve. That is itself a fact about these doors.
    await flows_mod.learn(spec, provider=ScriptedProvider(list(STEPS)), router=_mock_router(),
                          cache=cache)
    # The gate this door has and the raw ones do not, in one line: an unapproved flow is refused.
    with pytest.raises(flows_mod.NotApprovedError):
        await flows_mod.replay(spec, cache=cache, require_approved=True)
    flows_mod.approve(spec, cache=cache)
    data = await flows_mod.replay(spec, cache=cache, require_approved=True)
    print(f"gated door replayed green after approval: {str(data)[:60]}")
