"""End-to-end: LEARN the demo-shop flow with the scripted teacher, then REPLAY it from
cache with no LLM and confirm it reproduces the final state."""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url


async def test_learn_then_replay(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    url = index_url()

    learn = await run_cached(
        url, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn", headless=True
    )
    assert learn.success
    assert learn.mode == "learn"
    # The flow was persisted, with the 4 actionable steps (the 'done' step isn't stored).
    flow = cache.get(flow_key(GOAL, url))
    assert flow is not None
    assert len(flow.steps) == 4

    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    assert replay.success
    assert replay.mode == "replay"
    assert replay.llm_calls == 0  # the whole point: no LLM on replay
    assert SUCCESS_TEXT.lower() in replay.final_text.lower()


async def test_replay_miss_without_cache(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    report = await run_cached(
        index_url(), GOAL, None, cache, mode="replay", headless=True
    )
    assert report.mode == "miss"
    assert report.success is False
