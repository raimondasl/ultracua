"""Stuck detection: a discovery run that keeps acting without changing the page bails
after `stuck_limit` consecutive no-progress steps instead of burning the full step budget."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ultracua.cache import FlowCache
from ultracua.config import settings
from ultracua.flow import run_cached
from ultracua.types import Action, Observation

from benchmarks.shop_flow import index_url


class _ScrollForever:
    """Always scrolls — scrolling never changes the structural fingerprint, so the loop
    should be detected as stuck."""

    def __init__(self) -> None:
        self.calls = 0

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        self.calls += 1
        return Action(action="scroll", intent="scrolling, making no progress"), None


async def test_stuck_detection_stops_runaway_learn(tmp_path: Path) -> None:
    prov = _ScrollForever()
    cache = FlowCache(root=tmp_path)
    report = await run_cached(
        index_url(), "go in circles", prov, cache,
        mode="learn", headless=True, max_steps=20,
    )
    # Bailed after ~stuck_limit steps, far short of max_steps=20.
    assert prov.calls <= settings.stuck_limit + 1
    assert report.success is False
    assert any(t.meta.get("stuck") for t in report.traces)
