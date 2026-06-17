"""Run many flows concurrently as separate contexts in ONE browser (PLAN.md Phase 4
throughput). Per the research, 20-50 browser CONTEXTS per process are far lighter than
launching that many browser instances — `run_many` uses contexts, capped by `concurrency`.

This raises aggregate throughput (independent jobs / fan-out subtasks); it does not make a
single task faster (that's what cache replay does).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import async_playwright

from .cache import FlowCache
from .config import settings
from .flow import FlowReport, run_cached


async def run_many(
    tasks: list[dict],
    concurrency: Optional[int] = None,
    headless: Optional[bool] = None,
    cache: Optional[FlowCache] = None,
) -> list[FlowReport]:
    """Run each task concurrently, each in its own browser context, capped at `concurrency`.

    Each task is a kwargs dict for `run_cached` (at least `url` and `goal`; optionally
    `provider`, `mode`, `scope`, `verifier`, ...). Returns reports in task order; a task
    that raises yields a `miss` report rather than failing the whole batch.
    """
    cap = concurrency or settings.concurrency
    hl = settings.headless if headless is None else headless
    sem = asyncio.Semaphore(cap)
    results: list[Optional[FlowReport]] = [None] * len(tasks)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=hl)
        try:
            async def _one(i: int, task: dict) -> None:
                async with sem:
                    kwargs = dict(task)
                    kwargs.setdefault("cache", cache)
                    kwargs["browser"] = browser
                    kwargs["headless"] = hl
                    try:
                        results[i] = await run_cached(**kwargs)
                    except Exception as exc:  # noqa: BLE001
                        results[i] = FlowReport(
                            mode="miss", success=False, note=f"{type(exc).__name__}: {exc}"
                        )

            await asyncio.gather(*(_one(i, t) for i, t in enumerate(tasks)))
        finally:
            await browser.close()

    return [r for r in results if r is not None]
