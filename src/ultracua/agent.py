"""The Phase 0 agent loop: warm session -> snapshot -> decide -> act -> verify -> trace.

This is the "walking skeleton" from PLAN.md Phase 0 — LLM in the loop every step, no
cache yet. Its job is to prove the warm single-hop topology end to end and produce an
instrumented per-step latency breakdown that Phase 1's flow cache will be measured against.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from .browser import BrowserSession
from .config import settings
from .providers.base import Provider
from .timing import StepTrace
from .types import Action, Observation, StepResult
from .verify import state_changed

OnStep = Callable[[StepTrace, Observation, Action, Optional[StepResult]], None]


async def run_goal(
    url: str,
    goal: str,
    provider: Provider,
    max_steps: Optional[int] = None,
    headless: Optional[bool] = None,
    on_step: Optional[OnStep] = None,
) -> list[StepTrace]:
    max_steps = max_steps or settings.max_steps
    session = await BrowserSession(headless=headless).start()
    traces: list[StepTrace] = []
    history: list[str] = []
    try:
        nav = StepTrace(index=-1)
        with nav.measure("navigate"):
            await session.goto(url)
        traces.append(nav)

        for i in range(max_steps):
            tr = StepTrace(index=i)
            with tr.measure("snapshot"):
                obs = await session.snapshot()

            t0 = time.perf_counter()
            action, ttft = await provider.decide(goal, obs, history)
            llm_ms = (time.perf_counter() - t0) * 1000.0
            if ttft is not None:
                tr.add("ttft", ttft)
                tr.add("gen", max(0.0, llm_ms - ttft))
            else:
                tr.add("llm", llm_ms)
            tr.meta["action"] = action.model_dump(exclude_none=True)

            if action.action in ("done", "give_up"):
                tr.meta["stop"] = action.action
                traces.append(tr)
                if on_step:
                    on_step(tr, obs, action, None)
                break

            ok, note = True, ""
            with tr.measure("act"):
                try:
                    await session.act(action)
                except Exception as exc:  # noqa: BLE001 - surface any actuation failure
                    ok, note = False, f"{type(exc).__name__}: {exc}"

            with tr.measure("verify"):
                after = await session.snapshot()
                changed = state_changed(obs, after)

            result = StepResult(
                action=action, ok=ok, state_changed=changed, note=note
            )
            tr.meta["result"] = result.model_dump()
            history.append(
                f"{action.action}({action.ref or action.text or ''}) "
                f"-> {'ok' if ok else 'FAIL'}{'' if changed else ' [no change]'}"
            )
            traces.append(tr)
            if on_step:
                on_step(tr, obs, action, result)

        return traces
    finally:
        await session.close()
