"""Cached-flow orchestration: learn-once, replay-fast (PLAN.md Phase 1).

`run_cached` is the entry point:
  - cache MISS  -> LEARN: drive the Phase-0 loop with a provider, recording a resilient
    locator + intent per successful step, then persist the flow.
  - cache HIT   -> REPLAY: re-resolve each stored locator and actuate via Playwright with
    NO LLM, gated by locator resolution (the passive precondition). On drift, optionally
    SELF-HEAL the single broken step via one LLM call and re-cache (mode="auto").

This is the 5-10x lever: a replayed step costs actuation only (tens of ms), not an LLM
round-trip (seconds).

`prepare` (post-navigation) and `finalize` (pre-close) hooks let an environment seed a
deterministic instance and read an outcome (e.g. a MiniWoB++ reward); the finalize result
is stored on `FlowReport.extra["finalize"]`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .config import settings
from .locators import describe, resolve
from .providers.base import Provider
from .timing import StepTrace
from .types import Action
from .verify import state_changed

OnStep = Callable[[StepTrace], None]
Prepare = Callable[[BrowserSession], Awaitable[None]]
Finalize = Callable[[BrowserSession], Awaitable[Any]]


@dataclass
class FlowReport:
    mode: str  # "learn" | "replay" | "replay+heal" | "miss"
    success: bool
    traces: list[StepTrace] = field(default_factory=list)
    llm_calls: int = 0
    healed_steps: int = 0
    final_text: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def step_traces(self) -> list[StepTrace]:
        return [t for t in self.traces if t.index >= 0]

    @property
    def total_ms(self) -> float:
        return sum(t.total_ms for t in self.traces)

    @property
    def avg_step_ms(self) -> float:
        steps = self.step_traces
        return sum(t.total_ms for t in steps) / len(steps) if steps else 0.0


async def run_cached(
    url: str,
    goal: str,
    provider: Optional[Provider] = None,
    cache: Optional[FlowCache] = None,
    mode: str = "auto",  # "auto" | "learn" | "replay"
    max_steps: Optional[int] = None,
    headless: Optional[bool] = None,
    scope: str = "default",
    on_step: Optional[OnStep] = None,
    prepare: Optional[Prepare] = None,
    finalize: Optional[Finalize] = None,
) -> FlowReport:
    cache = cache or FlowCache()
    key = flow_key(goal, url, scope)
    cached = cache.get(key)

    if cached is not None and mode in ("auto", "replay"):
        heal_provider = provider if mode == "auto" else None
        report = await _replay(
            url, key, cached, cache, heal_provider, headless, on_step, prepare, finalize, goal
        )
        if report.success or mode == "replay":
            return report
        # auto-mode replay failed irrecoverably -> fall through to a fresh learn run.

    if mode == "replay":
        return FlowReport(mode="miss", success=False, note="no cached flow for key")

    if provider is None:
        return FlowReport(mode="miss", success=False, note="learn requires a provider")
    return await _learn(
        url, goal, key, provider, cache, max_steps, headless, on_step, prepare, finalize
    )


async def _learn(
    url: str,
    goal: str,
    key: str,
    provider: Provider,
    cache: FlowCache,
    max_steps: Optional[int],
    headless: Optional[bool],
    on_step: Optional[OnStep],
    prepare: Optional[Prepare],
    finalize: Optional[Finalize],
) -> FlowReport:
    max_steps = max_steps or settings.max_steps
    session = await BrowserSession(headless=headless).start()
    traces: list[StepTrace] = []
    history: list[str] = []
    steps: list[CachedStep] = []
    llm = 0
    success = False
    try:
        nav = StepTrace(index=-1)
        with nav.measure("navigate"):
            await session.goto(url)
            if prepare:
                await prepare(session)
        traces.append(nav)

        for i in range(max_steps):
            tr = StepTrace(index=i)
            with tr.measure("snapshot"):
                obs = await session.snapshot()

            t0 = time.perf_counter()
            action, ttft = await provider.decide(goal, obs, history)
            llm += 1
            llm_ms = (time.perf_counter() - t0) * 1000.0
            if ttft is not None:
                tr.add("ttft", ttft)
                tr.add("gen", max(0.0, llm_ms - ttft))
            else:
                tr.add("llm", llm_ms)
            tr.meta["action"] = action.model_dump(exclude_none=True)

            if action.action in ("done", "give_up"):
                success = action.action == "done"
                tr.meta["stop"] = action.action
                traces.append(tr)
                if on_step:
                    on_step(tr)
                break

            spec = None
            if action.action in ("click", "type") and action.ref:
                with tr.measure("describe"):
                    spec = await describe(session.page, action.ref)

            ok, note = True, ""
            with tr.measure("act"):
                try:
                    await session.act(action)
                except Exception as exc:  # noqa: BLE001
                    ok, note = False, f"{type(exc).__name__}: {exc}"

            with tr.measure("verify"):
                after = await session.snapshot()
                tr.meta["changed"] = state_changed(obs, after)

            if ok:
                steps.append(
                    CachedStep(
                        intent=action.intent,
                        action=action.action,
                        locator=spec,
                        text=action.text,
                        precond_fingerprint=obs.fingerprint,
                    )
                )
            history.append(f"{action.action} -> {'ok' if ok else 'FAIL ' + note}")
            traces.append(tr)
            if on_step:
                on_step(tr)

        extra = {"finalize": await finalize(session)} if finalize else {}
        final_text = await _body_text(session)
        if success and steps:
            cache.put(
                CachedFlow(
                    key=key, goal=goal, start_url=url, steps=steps, created_ts=time.time()
                )
            )
        return FlowReport(
            mode="learn",
            success=success,
            traces=traces,
            llm_calls=llm,
            final_text=final_text,
            extra=extra,
        )
    finally:
        await session.close()


async def _replay(
    url: str,
    key: str,
    flow: CachedFlow,
    cache: FlowCache,
    provider: Optional[Provider],
    headless: Optional[bool],
    on_step: Optional[OnStep],
    prepare: Optional[Prepare],
    finalize: Optional[Finalize],
    goal: str,
) -> FlowReport:
    session = await BrowserSession(headless=headless).start()
    traces: list[StepTrace] = []
    llm = 0
    healed = 0
    success = True
    mode = "replay"
    dirty = False
    try:
        nav = StepTrace(index=-1)
        with nav.measure("navigate"):
            await session.goto(url)
            if prepare:
                await prepare(session)
        traces.append(nav)

        for i, step in enumerate(flow.steps):
            tr = StepTrace(index=i)
            tr.meta["intent"] = step.intent
            tr.meta["action"] = step.action
            ok, note, did_heal = await _replay_step(session, step, provider, tr, goal)
            if did_heal:
                healed += 1
                llm += 1
                mode = "replay+heal"
                dirty = True
            tr.meta["ok"] = ok
            if note:
                tr.meta["note"] = note
            traces.append(tr)
            if on_step:
                on_step(tr)
            if not ok:
                success = False
                break

        extra = {"finalize": await finalize(session)} if finalize else {}
        final_text = await _body_text(session)
        if dirty and success:
            cache.put(flow)
        return FlowReport(
            mode=mode,
            success=success,
            traces=traces,
            llm_calls=llm,
            healed_steps=healed,
            final_text=final_text,
            extra=extra,
        )
    finally:
        await session.close()


async def _replay_step(
    session: BrowserSession,
    step: CachedStep,
    provider: Optional[Provider],
    tr: StepTrace,
    goal: str,
) -> tuple[bool, str, bool]:
    """Replay one cached step. Returns (ok, note, did_heal)."""
    page = session.page
    assert page is not None

    if step.action in ("press", "scroll", "navigate"):
        with tr.measure("act"):
            try:
                await session.act(
                    Action(action=step.action, intent=step.intent, text=step.text)
                )
                return True, "", False
            except Exception as exc:  # noqa: BLE001
                note = f"{type(exc).__name__}"
        return await _maybe_heal(session, step, provider, tr, goal, note)

    if step.action in ("click", "type") and step.locator is not None:
        with tr.measure("resolve"):
            loc = await resolve(page, step.locator)
        if loc is not None:
            with tr.measure("act"):
                try:
                    if step.action == "click":
                        await loc.click(timeout=settings.action_timeout_ms)
                    else:
                        await loc.fill(step.text or "", timeout=settings.action_timeout_ms)
                    return True, "", False
                except Exception as exc:  # noqa: BLE001
                    note = f"act failed: {type(exc).__name__}"
        else:
            note = "locator unresolved (drift)"
        return await _maybe_heal(session, step, provider, tr, goal, note)

    return False, "unreplayable step", False


async def _maybe_heal(
    session: BrowserSession,
    step: CachedStep,
    provider: Optional[Provider],
    tr: StepTrace,
    goal: str,
    note: str,
) -> tuple[bool, str, bool]:
    if provider is None:
        return False, note, False
    with tr.measure("heal_snapshot"):
        obs = await session.snapshot()
    hint = f"{goal} — specifically right now: {step.intent}"
    with tr.measure("heal_llm"):
        action, _ttft = await provider.decide(hint, obs, [])
    if action.action in ("done", "give_up"):
        return False, f"{note}; heal declined", True
    spec = None
    if action.action in ("click", "type") and action.ref:
        spec = await describe(session.page, action.ref)
    with tr.measure("heal_act"):
        try:
            await session.act(action)
        except Exception as exc:  # noqa: BLE001
            return False, f"{note}; heal act failed: {type(exc).__name__}", True
    if spec is not None:
        step.locator = spec
    if action.text is not None:
        step.text = action.text
    return True, f"healed ({note})", True


async def _body_text(session: BrowserSession) -> str:
    try:
        assert session.page is not None
        txt = await session.page.inner_text("body")
        return " ".join(txt.split())[:500]
    except Exception:
        return ""
