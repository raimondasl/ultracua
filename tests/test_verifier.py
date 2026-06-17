"""A completion verifier caches a solved flow that the agent didn't cleanly finish."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.types import Action, Observation
from ultracua.verifiers import keyword_completion

from benchmarks.shop_flow import index_url


class _ClickThenScroll:
    """Click one element, then scroll forever (never emits done) -> stuck-bails."""

    def __init__(self) -> None:
        self.n = 0

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        self.n += 1
        if self.n == 1 and obs.elements:
            return Action(action="click", intent="click first element", ref=obs.elements[0].ref), None
        return Action(action="scroll", intent="scrolling"), None


async def test_keyword_completion_signal() -> None:
    yes = Observation(url="u", title="t", elements=[], text="Order placed. Thank you!", fingerprint="f")
    no = Observation(url="u", title="t", elements=[], text="cart", fingerprint="f")
    assert await keyword_completion("buy a thing", yes) is True
    assert await keyword_completion("buy a thing", no) is None


async def test_verifier_caches_solved_flow(tmp_path: Path) -> None:
    async def always_done(goal: str, obs: Observation) -> Optional[bool]:
        return True

    cache = FlowCache(root=tmp_path)
    url = index_url()
    rep = await run_cached(
        url, "verify me", _ClickThenScroll(), cache, mode="learn", headless=True,
        verifier=always_done,
    )
    assert rep.success is True
    assert cache.get(flow_key("verify me", url)) is not None  # the click step was cached


async def test_no_verifier_leaves_stuck_flow_uncached(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path)
    url = index_url()
    rep = await run_cached(url, "verify me", _ClickThenScroll(), cache, mode="learn", headless=True)
    assert rep.success is False
    assert cache.get(flow_key("verify me", url)) is None
