"""$0 regression gate (#3a): deterministic guards that fail CI on a cost or fidelity regression,
without any real LLM. Driven by the scripted teacher over the canonical demo-shop flow.

It catches: replay accidentally calling the LLM (cost), the agent loop ballooning (call count),
the learned flow's structure changing (step count), and replay no longer reaching the goal (fidelity).
"""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url

_EXPECTED_STEPS = 4              # the demo-shop flow is 4 actuating steps (+ a `done`)
_MAX_LEARN_CALLS = len(STEPS)   # the scripted teacher is consulted once per scripted step (= 5)


async def test_demo_shop_learn_replay_stays_cheap_and_faithful(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()

    learn = await run_cached(url, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn", headless=True)
    assert learn.success
    assert learn.llm_calls <= _MAX_LEARN_CALLS                     # learn-cost ceiling (agent-loop calls)

    cached = cache.get(flow_key(GOAL, url))
    assert cached is not None and len(cached.steps) == _EXPECTED_STEPS  # structure didn't balloon

    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    assert replay.success
    assert replay.llm_calls == 0                                   # THE cost guarantee: 0-LLM replay
    assert SUCCESS_TEXT.lower() in replay.final_text.lower()       # fidelity: reached the goal state


async def test_replay_makes_no_llm_calls_even_after_repeats(tmp_path: Path) -> None:
    # A learned flow replayed repeatedly must stay 0-LLM every time (no creeping per-run model calls).
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await run_cached(url, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn", headless=True)
    for _ in range(3):
        r = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
        assert r.success and r.llm_calls == 0 and r.healed_steps == 0
