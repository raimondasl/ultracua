"""run_many runs flows concurrently across contexts in one browser, capped by concurrency."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ultracua.cache import FlowCache
from ultracua.parallel import run_many
from ultracua.types import Action, Observation

from benchmarks.shop_flow import index_url


class _ConcProvider:
    """Records how many decides run at once, then finishes immediately."""

    def __init__(self, shared: dict) -> None:
        self.shared = shared

    async def decide(
        self, goal: str, obs: Observation, history: list[str]
    ) -> tuple[Action, Optional[float]]:
        self.shared["active"] += 1
        self.shared["peak"] = max(self.shared["peak"], self.shared["active"])
        await asyncio.sleep(0.05)
        self.shared["active"] -= 1
        return Action(action="done", intent="done"), None


async def test_run_many_runs_concurrently_capped(tmp_path: Path) -> None:
    shared = {"active": 0, "peak": 0}
    n = 6
    tasks = [
        {"url": index_url(), "goal": f"task {i}", "provider": _ConcProvider(shared),
         "mode": "learn", "scope": f"s{i}"}
        for i in range(n)
    ]
    reports = await run_many(tasks, concurrency=3, headless=True, cache=FlowCache(root=tmp_path))

    assert len(reports) == n
    assert all(r.success for r in reports)   # each provider emitted done
    assert shared["peak"] <= 3               # concurrency cap respected
    assert shared["peak"] >= 2               # ...and real concurrency happened
