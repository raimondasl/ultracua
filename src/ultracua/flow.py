"""Cached-flow orchestration: learn-once, replay-fast (PLAN.md Phase 1) made safe (Phase 2).

`run_cached` is the entry point:
  - cache MISS  -> LEARN: drive the loop with a provider, recording a resilient locator +
    intent (+ a `mutating` flag) per step, then persist the flow.
  - cache HIT   -> REPLAY: re-resolve each stored locator and actuate via Playwright with
    NO LLM. On drift, SELF-HEAL the single broken step via one intent-keyed LLM call.

Phase 2 safety:
  - MUTATION GATE: irreversible steps (submit/pay/...) are never blind-replayed — they
    require a page-fingerprint match and carry an Idempotency-Key, so a stale cache or a
    retry can't fire a wrong/duplicate side effect.
  - INTERSTITIAL detection (CAPTCHA / anti-bot) escalates instead of burning retries.
  - PACING governor (per-origin concurrency + optional jitter) keeps speed off the wire.

`prepare` (post-nav) and `finalize` (pre-close) hooks let an environment seed a
deterministic instance and read an outcome; the finalize result lands in
`FlowReport.extra["finalize"]`.
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
from .safety import (
    PacingGovernor,
    idempotency_key,
    is_mutating,
    looks_like_interstitial,
    origin_of,
)
from .obs import UsageTotals, get_logger, new_run_id
from .timing import StepTrace
from .types import Action, Observation
from .verify import state_changed
from .webmcp import detect as _webmcp_detect

_log = get_logger("flow")

OnStep = Callable[[StepTrace], None]
Prepare = Callable[[BrowserSession], Awaitable[None]]
Finalize = Callable[[BrowserSession], Awaitable[Any]]
# Completion verifier: given the goal and the final observation, return True if the goal
# looks achieved (so a solved-but-not-`done` flow still gets cached); False/None otherwise.
Verifier = Callable[[str, Observation], Awaitable[Optional[bool]]]


@dataclass
class FlowReport:
    mode: str  # "learn" | "replay" | "replay+heal" | "miss" | "escalate"
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
    governor: Optional[PacingGovernor] = None,
    browser: Optional[Any] = None,
    verifier: Optional[Verifier] = None,
    grounding: Optional[Any] = None,
    record_har_path: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    storage_state: Optional[str] = None,
) -> FlowReport:
    cache = cache or FlowCache()
    governor = governor or PacingGovernor()
    key = flow_key(goal, url, scope)
    cached = cache.get(key)
    new_run_id()
    _log.info("run start: mode=%s cached=%s url=%s goal=%r", mode, cached is not None, url, goal)

    if cached is not None and mode in ("auto", "replay"):
        heal_provider = provider if mode == "auto" else None
        report = await _replay(
            url, key, cached, cache, heal_provider, headless, on_step,
            prepare, finalize, goal, governor, scope, browser, record_har_path, extra_headers,
            storage_state,
        )
        if report.success or mode == "replay" or report.mode == "escalate":
            return report
        # auto-mode replay failed irrecoverably -> fall through to a fresh learn run.

    if mode == "replay":
        return FlowReport(mode="miss", success=False, note="no cached flow for key")

    if provider is None and grounding is None:
        return FlowReport(mode="miss", success=False, note="learn requires a provider or grounding")
    return await _learn(
        url, goal, key, provider, cache, max_steps, headless, on_step,
        prepare, finalize, governor, scope, browser, verifier, grounding,
        record_har_path, extra_headers, storage_state,
    )


async def _is_interstitial(session: BrowserSession) -> bool:
    page = session.page
    assert page is not None
    try:
        title = await page.title()
        text = await page.inner_text("body")
    except Exception:
        return False
    return looks_like_interstitial(page.url, title, text)


async def _vision_decide(session: BrowserSession, goal: str, grounding: Any, tr: StepTrace):
    """Take a screenshot and ask the grounding model where to act (vision tier)."""
    with tr.measure("screenshot"):
        png = await session.screenshot()
    assert session.page is not None
    vp = session.page.viewport_size or {"width": 0, "height": 0}
    tr.meta["tier"] = "vision"
    return await grounding.decide(goal, png, vp)


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
    governor: PacingGovernor,
    scope: str,
    browser: Optional[Any] = None,
    verifier: Optional[Verifier] = None,
    grounding: Optional[Any] = None,
    record_har_path: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    storage_state: Optional[str] = None,
) -> FlowReport:
    max_steps = max_steps or settings.max_steps
    _router = getattr(provider, "router", None)  # for per-run token/cost accounting, if available
    _usnap = _router.totals.snapshot() if _router is not None else None
    session = await BrowserSession(
        headless=headless, browser=browser, record_har_path=record_har_path,
        storage_state=storage_state,
    ).start()
    traces: list[StepTrace] = []
    history: list[str] = []
    steps: list[CachedStep] = []
    llm = 0
    success = False
    no_progress = 0
    try:
        # Auth/setup headers must be on the context BEFORE the first navigation (e.g. a
        # Magento auto-login header on the initial admin request).
        if extra_headers:
            await session.set_extra_http_headers(extra_headers)
        nav = StepTrace(index=-1)
        with nav.measure("navigate"):
            await session.goto(url)
            if prepare:
                await prepare(session)
        traces.append(nav)

        if await _is_interstitial(session):
            return FlowReport(mode="escalate", success=False, traces=traces,
                              note="interstitial/CAPTCHA detected", extra={"escalate": True})

        for i in range(max_steps):
            tr = StepTrace(index=i)
            with tr.measure("snapshot"):
                obs = await session.snapshot()

            # Surface any WebMCP tools the page exposes so the agent can call them directly.
            obs.webmcp_tools = await _webmcp_detect(session.page)

            # Pick the action source: VISION when the DOM has nothing to address, else the
            # DOM provider — which may itself request vision (need_vision) or a webmcp_call.
            t0 = time.perf_counter()
            if not obs.elements and grounding is not None:
                action, ttft = await _vision_decide(session, goal, grounding, tr)
            elif provider is not None:
                action, ttft = await provider.decide(goal, obs, history)
                if action.action == "need_vision":
                    if grounding is not None:
                        action, ttft = await _vision_decide(session, goal, grounding, tr)
                    else:
                        action = Action(action="give_up", intent="vision requested but unavailable")
            else:
                action, ttft = Action(action="give_up", intent="no target and no provider"), None
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
            origin = origin_of(session.page.url)
            with tr.measure("act"):
                try:
                    async with governor.gate(origin):
                        await session.act(action)
                except Exception as exc:  # noqa: BLE001
                    ok, note = False, f"{type(exc).__name__}: {exc}"

            with tr.measure("verify"):
                after = await session.snapshot()
                changed = state_changed(obs, after)
                tr.meta["changed"] = changed
            no_progress = 0 if (ok and changed) else no_progress + 1

            if ok:
                name = spec.name if spec else ""
                steps.append(
                    CachedStep(
                        intent=action.intent,
                        action=action.action,
                        locator=spec,
                        text=action.text,
                        coords=action.coords,
                        tool=action.tool,
                        args=action.args,
                        precond_fingerprint=obs.fingerprint,
                        mutating=is_mutating(action.action, action.intent, name),
                    )
                )
            desc = action.action
            if action.ref:
                desc += f" {action.ref}"
            if action.text:
                desc += f" {action.text!r}"
            history.append(f"{desc} -> {'ok' if ok else 'FAIL ' + note}")
            traces.append(tr)
            if on_step:
                on_step(tr)

            if no_progress >= settings.stuck_limit:
                tr.meta["stuck"] = no_progress  # bail: agent looping without progress
                break

        # The agent didn't cleanly emit `done` but took real steps — ask the verifier whether
        # the goal is actually met (e.g. fast tier solved it but didn't recognize completion).
        if not success and steps and verifier is not None:
            try:
                if await verifier(goal, await session.snapshot()):
                    success = True
            except Exception:  # noqa: BLE001
                pass

        fin = await finalize(session) if finalize else None
        # A finalize hook may itself signal completion (e.g. a data-read task that "solved" via
        # final full-text extraction without the agent ever emitting `done`) — cache the flow so
        # it can replay. The agent's observation is a short snippet, so this full-text signal is
        # more reliable than an observation-based verifier for retrieval tasks.
        if not success and steps and isinstance(fin, dict) and fin.get("solved"):
            success = True
        extra = {"finalize": fin} if finalize else {}
        final_text = await _body_text(session)
        if success and steps:
            cache.put(
                CachedFlow(
                    key=key, goal=goal, start_url=url, steps=steps, created_ts=time.time()
                )
            )
        used = _router.totals.since(_usnap) if _router is not None else None
        if used is not None:
            extra["usage"] = used.as_dict(settings.model)
        _log.info(
            "learn done: success=%s steps=%d llm_calls=%d cached=%s%s",
            success, len(steps), llm, success and bool(steps),
            f" usage=[{used.summary(settings.model)}]" if used is not None else "",
        )
        return FlowReport(
            mode="learn", success=success, traces=traces, llm_calls=llm,
            final_text=final_text, extra=extra,
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
    governor: PacingGovernor,
    scope: str,
    browser: Optional[Any] = None,
    record_har_path: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    storage_state: Optional[str] = None,
) -> FlowReport:
    session = await BrowserSession(
        headless=headless, browser=browser, record_har_path=record_har_path,
        storage_state=storage_state,
    ).start()
    traces: list[StepTrace] = []
    llm = 0
    healed = 0
    success = True
    mode = "replay"
    dirty = False
    try:
        if extra_headers:
            await session.set_extra_http_headers(extra_headers)
        nav = StepTrace(index=-1)
        with nav.measure("navigate"):
            await session.goto(url)
            if prepare:
                await prepare(session)
        traces.append(nav)

        if await _is_interstitial(session):
            _log.warning("replay: interstitial/CAPTCHA detected — escalating instead of retrying")
            return FlowReport(mode="escalate", success=False, traces=traces,
                              note="interstitial/CAPTCHA detected", extra={"escalate": True})

        for i, step in enumerate(flow.steps):
            tr = StepTrace(index=i)
            tr.meta["intent"] = step.intent
            tr.meta["action"] = step.action
            if step.mutating:
                tr.meta["mutating"] = True
            ok, note, did_heal = await _replay_step(
                session, step, provider, tr, goal, governor, scope, i
            )
            if did_heal:
                healed += 1
                llm += 1
                mode = "replay+heal"
                dirty = True
                _log.info("replay: step %d %r self-healed (%s)", i, step.intent, note)
            tr.meta["ok"] = ok
            if note:
                tr.meta["note"] = note
            traces.append(tr)
            if on_step:
                on_step(tr)
            if not ok:
                success = False
                _log.warning("replay: step %d %r failed: %s", i, step.intent, note)
                break

        extra = {"finalize": await finalize(session)} if finalize else {}
        final_text = await _body_text(session)
        if dirty and success:
            cache.put(flow)
        _log.info("replay done: mode=%s success=%s healed=%d steps=%d",
                  mode, success, healed, len(flow.steps))
        return FlowReport(
            mode=mode, success=success, traces=traces, llm_calls=llm,
            healed_steps=healed, final_text=final_text, extra=extra,
        )
    finally:
        await session.close()


async def _replay_step(
    session: BrowserSession,
    step: CachedStep,
    provider: Optional[Provider],
    tr: StepTrace,
    goal: str,
    governor: PacingGovernor,
    scope: str,
    idx: int,
) -> tuple[bool, str, bool]:
    """Replay one cached step. Returns (ok, note, did_heal)."""
    page = session.page
    assert page is not None
    origin = origin_of(page.url)

    # MUTATION GATE — never blind-replay an irreversible action under page drift, and never let
    # an LLM re-drive a write under uncertainty: on drift a mutating step FAILS LOUD (it is not
    # healed). A failed write is the caller's to re-learn + re-approve, never to guess at.
    if step.mutating:
        with tr.measure("gate"):
            obs = await session.snapshot()
        if step.precond_fingerprint and obs.fingerprint != step.precond_fingerprint:
            tr.meta["gate"] = "drift"
            return False, "mutation gate: page drift — refusing to re-drive a write", False
        key = idempotency_key(scope, idx, step.intent)
        tr.meta["idempotency_key"] = key
        await session.set_extra_http_headers({"Idempotency-Key": key})

    try:
        if step.action in ("press", "scroll", "navigate", "click_xy", "webmcp_call"):
            note = ""
            async with governor.gate(origin):
                with tr.measure("act"):
                    try:
                        await session.act(
                            Action(
                                action=step.action, intent=step.intent, text=step.text,
                                coords=step.coords, tool=step.tool, args=step.args,
                            )
                        )
                        return True, "", False
                    except Exception as exc:  # noqa: BLE001
                        note = f"{type(exc).__name__}"
            return await _maybe_heal(session, step, provider, tr, goal, note)

        if step.action in ("click", "type") and step.locator is not None:
            with tr.measure("resolve"):
                loc = await resolve(page, step.locator)
            if loc is None:
                return await _maybe_heal(
                    session, step, provider, tr, goal, "locator unresolved (drift)"
                )
            note = ""
            async with governor.gate(origin):
                with tr.measure("act"):
                    try:
                        if step.action == "click":
                            await loc.click(timeout=settings.action_timeout_ms)
                        else:
                            await loc.fill(step.text or "", timeout=settings.action_timeout_ms)
                        return True, "", False
                    except Exception as exc:  # noqa: BLE001
                        note = f"act failed: {type(exc).__name__}"
            return await _maybe_heal(session, step, provider, tr, goal, note)

        return False, "unreplayable step", False
    finally:
        if step.mutating:
            await session.set_extra_http_headers({})  # clear the idempotency header


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
