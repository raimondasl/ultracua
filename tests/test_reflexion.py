"""Reflexion retry (Tier-2): when a best-of-N attempt fails, summarize WHY into one LLM-written lesson
and feed it to the NEXT attempt — learning from the failure instead of resampling blindly. The lesson
rides in the AUTHORING goal only; the cache key and stored `CachedFlow.goal` stay the original.

Key-less: the reflection LLM call (`_reflect`) is monkeypatched; a recording provider checks the lesson
reaches the next attempt and that reflect=off changes nothing.
"""

from __future__ import annotations

from pathlib import Path

import ultracua.flow as flowmod
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import FlowReport, _reflect, run_cached
from ultracua.providers.scripted import ScriptedProvider
from ultracua.types import Action

from benchmarks.shop_flow import GOAL, index_url

# A pure READ flow (benign intents -> verify-by-replay runs; no write).
_READ_STEPS = [
    {"action": "type", "role": "textbox", "name": "search", "text": "widget", "intent": "enter the query"},
    {"action": "click", "role": "button", "name": "search", "intent": "run the search"},
    {"action": "click", "role": "link", "name": "open widget x", "intent": "open the detail page"},
    {"action": "click", "role": "button", "name": "add to cart", "intent": "click the cart button"},
    {"action": "done", "intent": "done"},
]


class _RecordGiveUpThenSucceed:
    """Records the GOAL seen at each attempt's first step; bails attempt 1, authors the good flow after."""

    def __init__(self, good_steps):
        self.good = good_steps
        self.attempt = 0
        self.sp = None
        self.goals_seen: list[str] = []

    async def decide(self, goal, obs, history):
        if not history:  # start of a new authoring attempt
            self.attempt += 1
            self.goals_seen.append(goal)
            self.sp = ScriptedProvider(list(self.good)) if self.attempt >= 2 else None
        if self.attempt < 2:
            return Action(action="give_up", intent="bail"), None
        return await self.sp.decide(goal, obs, history)


_LESSON = "REFLECTION_TRY_THE_SEARCH_BUTTON_FIRST"


async def test_reflexion_feeds_the_lesson_into_the_next_attempt(tmp_path: Path, monkeypatch) -> None:
    async def _fake_reflect(provider, goal, report):
        return _LESSON

    monkeypatch.setattr(flowmod, "_reflect", _fake_reflect)
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    provider = _RecordGiveUpThenSucceed(_READ_STEPS)
    report = await run_cached(url, GOAL, provider, cache, mode="learn", headless=True,
                              samples=3, verify_replay=True, reflect=True)

    assert report.success and len(provider.goals_seen) == 2
    assert _LESSON not in provider.goals_seen[0]   # attempt 1 had no lesson
    assert _LESSON in provider.goals_seen[1]       # attempt 2 was fed the reflection
    assert report.extra.get("reflections") == [_LESSON]
    # The lesson must NOT leak into the cache key or the stored goal — replay still keys on the original.
    cached = cache.get(flow_key(GOAL, url))
    assert cached is not None and cached.goal == GOAL


async def test_reflexion_off_changes_nothing(tmp_path: Path, monkeypatch) -> None:
    calls = {"n": 0}

    async def _spy(provider, goal, report):
        calls["n"] += 1
        return _LESSON

    monkeypatch.setattr(flowmod, "_reflect", _spy)
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    provider = _RecordGiveUpThenSucceed(_READ_STEPS)
    report = await run_cached(url, GOAL, provider, cache, mode="learn", headless=True,
                              samples=3, verify_replay=True, reflect=False)  # OFF

    assert report.success and calls["n"] == 0          # _reflect never consulted
    assert _LESSON not in provider.goals_seen[1]       # the next attempt's goal is unchanged


async def test_reflect_degrades_gracefully_without_a_router() -> None:
    class _NoRouter:
        pass

    # No `.router` attribute -> reflexion returns None (falls back to plain best-of-N), never raises.
    assert await _reflect(_NoRouter(), "goal", FlowReport(mode="learn", success=False)) is None
