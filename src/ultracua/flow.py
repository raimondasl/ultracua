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

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .conditions import condition_present
from .config import settings
from .locators import describe, focused_ref, resolve
from .providers.base import Provider
from .safety import (
    PacingGovernor,
    classify_mutation,
    idempotency_key,
    is_write_request,
    looks_like_interstitial,
    origin_of,
)
from .obs import UsageTotals, get_logger, new_run_id
from .snapshot import mutation_context, scope_fingerprint
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
    verify_replay: bool = False,
    samples: int = 1,
    reflect: bool = False,
    window_size: Optional[tuple[int, int]] = None,
) -> FlowReport:
    cache = cache or FlowCache()
    governor = governor or PacingGovernor()
    key = flow_key(goal, url, scope)
    cached = cache.get(key)
    new_run_id()
    _log.info("run start: mode=%s cached=%s url=%s goal=%r", mode, cached is not None, url, goal)

    if cached is not None and mode in ("auto", "replay", "repair"):
        # "repair" = replay WITH the heal provider (self-heal + suffix-replan a drifted tail), but
        # NO fall-through to a full re-author — the caller owns whole-flow relearn (and its metadata).
        heal_provider = provider if mode in ("auto", "repair") else None
        report = await _replay(
            url, key, cached, cache, heal_provider, headless, on_step,
            prepare, finalize, goal, governor, scope, browser, record_har_path, extra_headers,
            storage_state, window_size=window_size,
        )
        if report.success or mode in ("replay", "repair") or report.mode == "escalate":
            return report
        # auto-mode replay failed irrecoverably -> fall through to a fresh learn run.

    if mode in ("replay", "repair"):
        return FlowReport(mode="miss", success=False, note="no cached flow for key")

    if provider is None and grounding is None:
        return FlowReport(mode="miss", success=False, note="learn requires a provider or grounding")
    if samples and samples > 1:  # best-of-N: re-author up to N times, keep the first verified sample
        return await _learn_n(
            url, goal, key, provider, cache, max_steps, headless, on_step,
            prepare, finalize, governor, scope, browser, verifier, grounding,
            record_har_path, extra_headers, storage_state, verify_replay, samples, reflect,
            window_size=window_size,
        )
    return await _learn(
        url, goal, key, provider, cache, max_steps, headless, on_step,
        prepare, finalize, governor, scope, browser, verifier, grounding,
        record_har_path, extra_headers, storage_state, verify_replay,
        window_size=window_size,
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


async def _author_steps(
    session: BrowserSession, goal: str, provider: Optional[Provider], governor: PacingGovernor,
    max_steps: int, on_step: Optional[OnStep] = None, grounding: Optional[Any] = None,
    block_mutations: bool = False,
) -> "tuple[list[CachedStep], bool, int, list[StepTrace], bool]":
    """Drive the agent from the CURRENT page to author replayable steps toward `goal`.

    The shared discovery loop: `_learn` calls it after navigating to the start URL; the suffix-replan
    in `_replay` calls it from a mid-flow page to re-author just the broken tail. Returns
    (steps, success, llm_calls, traces, performed_write).

    `performed_write` answers "did a write fire ON THE WIRE this pass?" — NOT just "is a mutating step in
    the recipe". Best-of-N / verify-by-replay must never re-run after a write (double-submit), and the
    recipe's `mutating` flags miss Enter-submits and formless JS POSTs, so it also watches the network for
    a write signature. That watcher counts a non-idempotent (POST/PUT/PATCH/DELETE) request when BOTH:
      - it is NOT to a known telemetry/analytics host (`safety.is_write_request`) — so a click that also
        fires a GA/Segment/Sentry beacon isn't mistaken for a write; and
      - it fired inside the ACT WINDOW (from just before `session.act` through the verify snapshot, plus a
        short grace) — so a background/1st-party beacon on a timer, firing during the LLM `decide()` gap,
        doesn't count even when it isn't on a known vendor host.
    This is ORIGIN-INDEPENDENT (it dropped the old same-origin requirement): a cross-origin write to a
    3rd-party payment/API host now counts, closing the gap where best-of-N could re-author and double-
    submit it; and it no longer hinges on `origin_of(page.url)`, which a mid-navigation blank URL could
    skew into missing a genuine same-origin write. Residual bound (the safe direction): a click-triggered
    non-telemetry read-POST — a same/cross-origin GraphQL/RPC query — is over-counted as a write, which
    only costs a re-sample, never a double-submit.

    `block_mutations=True` (used by the replan path) refuses to EXECUTE any mutating action: a
    replay-triggered re-author must never perform a NEW write — it isn't approved and could
    double-submit. On a mutating decision the loop aborts (no act) so the caller fails loud / escalates
    to a human re-learn. (Learning a write flow uses `block_mutations=False` — the write is intended.)
    """
    traces: list[StepTrace] = []
    history: list[str] = []
    steps: list[CachedStep] = []
    llm = 0
    success = False
    no_progress = 0
    wrote = {"hit": False}  # did a write fire on the wire? (set by the request watcher + pre-act, below)
    # Act window: a request is attributed to a user action only while this is open (just before
    # `session.act` through the verify snapshot) or within `write_window_ms` after it closes. Outside
    # the window — during the initial snapshot / LLM `decide()` — requests are background noise and ignored.
    act_window = {"open": False, "until": 0.0}
    page = session.page

    def _in_act_window() -> bool:
        return act_window["open"] or time.monotonic() <= act_window["until"]

    def _watch_request(req):  # a non-idempotent, non-telemetry request fired in causal response = a write
        try:
            if _in_act_window() and is_write_request(req.method, req.url):
                wrote["hit"] = True
        except Exception:  # noqa: BLE001
            pass

    if page is not None:
        page.on("request", _watch_request)
    for i in range(max_steps):
        tr = StepTrace(index=i)
        with tr.measure("snapshot"):
            obs = await session.snapshot()

        # Surface any WebMCP tools the page exposes so the agent can call them directly.
        obs.webmcp_tools = await _webmcp_detect(session.page)

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
        precond_scope = ""
        ctx: dict = {}
        if action.action in ("click", "type") and action.ref:
            with tr.measure("describe"):
                spec = await describe(session.page, action.ref)
            if action.action == "click":  # structural write-signal (does it submit a form? method?)
                ctx = await mutation_context(
                    session.page.locator(f'[data-ultracua-ref="{action.ref}"]').first
                )
        mutating = classify_mutation(action.action, action.intent, spec.name if spec else "", ctx)
        if block_mutations and mutating:
            # A replay-triggered re-author must NOT perform a new write — abort before acting.
            tr.meta["blocked"] = "mutation-under-replan"
            traces.append(tr)
            if on_step:
                on_step(tr)
            break
        # For a mutating step, record the PRECISE precondition (the target's form/section) now,
        # while the element is still present — the gate checks this at replay.
        if mutating and action.action in ("click", "type") and action.ref:
            precond_scope = await scope_fingerprint(
                session.page.locator(f'[data-ultracua-ref="{action.ref}"]').first
            )
        elif mutating and action.action == "press":
            # A refless submit (Enter) has no ref of its own — anchor the precondition on the FOCUSED
            # field (the submit context) BY IDENTITY: capture its locator so replay re-resolves and
            # re-focuses that exact element and gates on ITS form/section. (Anchoring on activeElement
            # at replay would instead fingerprint whatever happens to be focused — not identity-stable.)
            # `focused_ref` fails closed (None) on a stale/ambiguous ref, so we never store a WRONG
            # locator — we fall back to the whole-page gate instead.
            ref = await focused_ref(session.page)
            if ref:
                spec = await describe(session.page, ref)  # stored as the press step's locator
                if spec is not None:
                    precond_scope = await scope_fingerprint(
                        session.page.locator(f'[data-ultracua-ref="{ref}"]').first
                    )

        ok, note = True, ""
        origin = origin_of(session.page.url)
        if mutating:  # flag BEFORE acting: a click that commits the write then times out still counts
            wrote["hit"] = True
        act_window["open"] = True  # OPEN: from here through verify, a wire write is attributed to this act
        with tr.measure("act"):
            try:
                async with governor.gate(origin):
                    await session.act(action)
            except Exception as exc:  # noqa: BLE001
                ok, note = False, f"{type(exc).__name__}: {exc}"

        with tr.measure("verify"):
            try:
                after = await session.snapshot()
                changed = state_changed(obs, after)
            except Exception:  # noqa: BLE001 - a post-action navigation can race the snapshot; don't
                changed = True  #               lose the attempt (and the write flag) to a transient throw
            tr.meta["changed"] = changed
        # CLOSE with a grace tail: a write's POST can race the post-act navigation and land just after the
        # verify snapshot returns; the grace keeps it attributed to THIS action, not silently dropped.
        act_window["open"] = False
        act_window["until"] = time.monotonic() + settings.write_window_ms / 1000.0
        no_progress = 0 if (ok and changed) else no_progress + 1

        if ok:
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
                    precond_scope=precond_scope,
                    mutating=mutating,
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
    if page is not None:
        try:
            page.remove_listener("request", _watch_request)
        except Exception:  # noqa: BLE001
            pass
    return steps, success, llm, traces, wrote["hit"] or any(s.mutating for s in steps)


async def _verify_by_replay(
    url: str, key: str, candidate: CachedFlow, cache: FlowCache, headless: Optional[bool],
    prepare: Optional[Prepare], governor: PacingGovernor, scope: str, browser: Optional[Any],
    extra_headers: Optional[dict], storage_state: Optional[str],
) -> bool:
    """Re-run a freshly-authored flow 0-LLM on a FRESH session; True iff every step reproduces.

    Navigation-fidelity only — provider=None (no heal/replan, no LLM) and finalize=None (no extraction,
    so it's cheap and adds no paid call). Catches the dominant discovery failure: an authored flow that
    looked solved in-session but whose cached locators don't survive a fresh load. The caller skips this
    for write flows (re-firing a mutating step would double-submit).
    """
    report = await _replay(
        url, key, candidate, cache, None, headless, None, prepare, None, candidate.goal,
        governor, scope, browser, None, extra_headers, storage_state,
    )
    return report.success


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
    verify_replay: bool = False,
    reflections: Optional[list] = None,
    window_size: Optional[tuple[int, int]] = None,
) -> FlowReport:
    max_steps = max_steps or settings.max_steps
    _router = getattr(provider, "router", None)  # for per-run token/cost accounting, if available
    _usnap = _router.totals.snapshot() if _router is not None else None
    session = await BrowserSession(
        headless=headless, browser=browser, record_har_path=record_har_path,
        storage_state=storage_state, window_size=window_size,
    ).start()
    traces: list[StepTrace] = []
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

        # Reflexion: prior failed attempts' lessons ride in the AUTHORING goal only — never the cache key
        # or the stored `CachedFlow.goal` (those stay the original `goal`), so replay still keys correctly.
        author_goal = goal
        if reflections:
            author_goal = goal + "\n\nLESSONS FROM PRIOR FAILED ATTEMPTS (do not repeat these mistakes):\n" + \
                "\n".join(f"- {r}" for r in reflections)
        steps, success, llm, step_traces, performed_write = await _author_steps(
            session, author_goal, provider, governor, max_steps, on_step=on_step, grounding=grounding,
        )
        traces.extend(step_traces)

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
        cached_here = False
        if success and steps:
            candidate = CachedFlow(key=key, goal=goal, start_url=url, steps=steps, created_ts=time.time())
            # VERIFY-BY-REPLAY: a flow can look "solved" in-session yet not reproduce — its cached
            # locators may have leaned on learn-time state. Before caching, replay it 0-LLM on a FRESH
            # session and cache ONLY if every step reproduces; otherwise fail loud (don't cache a flow
            # that won't replay). Write flows are exempt — re-replaying a step that fired a write would
            # double-submit; they cache on the Phase-D confirm check and are approval-gated. The gate
            # keys off `performed_write` (a write fired on the wire), NOT the recipe's `mutating` flags,
            # which miss Enter-submits and formless JS POSTs.
            if verify_replay and not performed_write:
                if await _verify_by_replay(url, key, candidate, cache, headless, prepare, governor,
                                           scope, browser, extra_headers, storage_state):
                    cache.put(candidate)
                    extra["verify"] = "passed"
                    cached_here = True
                else:
                    success = False
                    extra["verify"] = "failed"
                    _log.warning("learn: authored flow did NOT survive verify-by-replay — not cached")
            else:
                cache.put(candidate)
                cached_here = True
        # `cached`: did THIS attempt cache a flow (vs a pre-existing one)? `performed_write`: did a write
        # fire on the wire? Best-of-N uses both to stop the loop and to NEVER re-author after a write.
        extra["cached"] = cached_here
        extra["performed_write"] = performed_write
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


def _trajectory(report: FlowReport) -> str:
    """A terse, model-readable summary of what a failed authoring attempt did (for the reflection)."""
    lines = []
    for tr in report.step_traces:
        a = tr.meta.get("action") or {}
        bits = []
        if tr.meta.get("changed") is False:
            bits.append("no page change")
        for k in ("stop", "stuck", "blocked"):
            if tr.meta.get(k):
                bits.append(f"{k}={tr.meta[k]}")
        lines.append(f"  {a.get('action', '?')} ({a.get('intent', '')})"
                     + (f" -> {', '.join(bits)}" if bits else ""))
    return "\n".join(lines) or "  (no actions taken)"


async def _reflect(provider: Provider, goal: str, report: FlowReport) -> Optional[str]:
    """One LLM call: given a FAILED authoring attempt, return 1-2 sentences of concrete advice for the
    next attempt. Returns None if the provider has no router or the call fails (reflexion degrades to
    plain best-of-N). Cost lands in the router totals, so `_learn_n` counts it."""
    router = getattr(provider, "router", None)
    if router is None:
        return None
    if report.extra.get("verify") == "failed":
        reason = "authored a flow, but it did NOT reproduce on a fresh 0-LLM replay (unstable locators / non-deterministic path)."
    elif not report.step_traces:
        reason = "took no useful action."
    else:
        reason = "did not reach a verified, replayable flow."
    from .llm.types import LLMRequest, Message, TextBlock
    req = LLMRequest(
        system=("You review a FAILED browser-automation attempt so the NEXT attempt succeeds. Reply with "
                "ONE or TWO sentences of concrete, actionable advice — specific elements or actions to try "
                "or avoid. Do not restate the goal or apologize."),
        messages=[Message("user", [TextBlock(
            f"GOAL: {goal}\n\nWHAT THE FAILED ATTEMPT DID:\n{_trajectory(report)}\n\n"
            f"OUTCOME: it {reason}\n\nFINAL PAGE (excerpt): {report.final_text[:400]}\n\n"
            "ADVICE FOR THE NEXT ATTEMPT:")])],
        max_tokens=160, temperature=settings.authoring_temperature,
    )
    try:
        txt = (await router.complete(req, tier="strong")).text().strip()
        return txt[:400] or None
    except Exception:  # noqa: BLE001 - reflexion is best-effort; fall back to plain re-sampling
        return None


async def _learn_n(
    url: str, goal: str, key: str, provider: Provider, cache: FlowCache, max_steps: Optional[int],
    headless: Optional[bool], on_step: Optional[OnStep], prepare: Optional[Prepare],
    finalize: Optional[Finalize], governor: PacingGovernor, scope: str, browser: Optional[Any] = None,
    verifier: Optional[Verifier] = None, grounding: Optional[Any] = None,
    record_har_path: Optional[str] = None, extra_headers: Optional[dict] = None,
    storage_state: Optional[str] = None, verify_replay: bool = False, samples: int = 1,
    reflect: bool = False, window_size: Optional[tuple[int, int]] = None,
) -> FlowReport:
    """Best-of-N authoring: re-author up to `samples` times and keep the FIRST sample the verify-by-replay
    oracle confirms. Each attempt is a fresh `_learn` (fresh session -> the LLM resamples at
    `settings.authoring_temperature`, default 1.0), converting discovery variance into a higher first-run
    success rate. With `reflect=True`, a failed attempt is summarized into one LLM-written lesson that is
    fed to the NEXT attempt — learning from the failure instead of resampling blindly (attacks the tasks
    that blind best-of-N plateaus on). READ-ONLY by design: the loop STOPS as soon as a flow is cached
    (verified read, or a write that cached on its confirm check), a write fired on the wire
    (`performed_write`), or an attempt raised — re-authoring after a write would re-submit. Usage is
    reported cumulatively across attempts. (Needs `verify_replay=True` to actually retry.)
    """
    samples = max(1, samples)
    _router = getattr(provider, "router", None)
    _usnap = _router.totals.snapshot() if _router is not None else None
    last: Optional[FlowReport] = None
    used = 0
    reflections: list = []
    for attempt in range(samples):
        used = attempt + 1
        try:
            last = await _learn(
                url, goal, key, provider, cache, max_steps, headless, on_step, prepare, finalize,
                governor, scope, browser, verifier, grounding, record_har_path, extra_headers,
                storage_state, verify_replay, reflections or None, window_size,
            )
        except Exception:  # an attempt that raised mid-way may have fired a write — never silently retry
            _log.warning("best-of-N: attempt %d raised — stopping (a write may have fired)", used)
            raise
        if last.extra.get("cached") or last.extra.get("performed_write"):
            break  # THIS attempt cached a (verified) read OR a write fired -> never re-author
        if reflect and attempt + 1 < samples:  # learn from this failure before the next sample
            lesson = await _reflect(provider, goal, last)
            if lesson:
                reflections.append(lesson)
    if last is not None:
        last.extra["samples_used"] = used
        if reflections:
            last.extra["reflections"] = list(reflections)
        if _router is not None:  # cumulative cost across every attempt (incl. reflection calls)
            last.extra["usage"] = _router.totals.since(_usnap).as_dict(settings.model)
    return last if last is not None else FlowReport(mode="learn", success=False)


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
    window_size: Optional[tuple[int, int]] = None,
) -> FlowReport:
    session = await BrowserSession(
        headless=headless, browser=browser, record_har_path=record_har_path,
        storage_state=storage_state, window_size=window_size,
    ).start()
    traces: list[StepTrace] = []
    llm = 0
    healed = 0
    success = True
    mode = "replay"
    dirty = False
    replanned = False
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
            wc = step.confirm if (step.mutating and step.confirm is not None) else None  # Phase G barrier
            if step.mutating:
                tr.meta["mutating"] = True
            # COMMIT-BARRIER BASELINE (Phase G): snapshot whether the per-write confirm ALREADY holds BEFORE
            # the write actuates. The barrier requires an absent->present TRANSITION, so a confirm that is
            # already satisfied (a persistent/shared status region, residual text from an earlier write, a
            # URL that doesn't change) can't be a false PASS — and a write that fires nothing leaves the
            # confirm absent, so the barrier fails loud. (timeout_ms=0: a single immediate check.)
            pre_confirm = False
            if wc is not None and wc.has_confirm():
                pre_confirm = await condition_present(
                    session.page, selector=wc.confirm_selector,
                    text_contains=wc.confirm_text_contains, url_contains=wc.confirm_url_contains,
                    timeout_ms=0,
                )
            ok, note, did_heal = await _replay_step(
                session, step, provider, tr, goal, governor, scope, i
            )
            # COMMIT BARRIER: a write with a per-step confirm must show its completion signal TRANSITION to
            # present before we proceed. A failure flips `ok` to False and falls into the existing fail-loud
            # path below — and since the step is `mutating`, suffix-replan is skipped, so replay STOPS here
            # (never silently running the next write). If the confirm was already true pre-write, this write's
            # completion can't be distinguished from prior state -> fail loud (the author must pick a confirm
            # unique to this write's outcome). Reuses the same `condition_present` the whole-flow confirm uses.
            if ok and wc is not None and wc.has_confirm():
                with tr.measure("confirm"):
                    try:
                        await session.page.wait_for_load_state("networkidle", timeout=wc.timeout_ms)
                    except Exception:  # noqa: BLE001
                        pass
                    landed = (not pre_confirm) and await condition_present(
                        session.page, selector=wc.confirm_selector,
                        text_contains=wc.confirm_text_contains, url_contains=wc.confirm_url_contains,
                        timeout_ms=wc.timeout_ms,
                    )
                if not landed:
                    ok = False
                    note = (f"write step {i} did not confirm (commit barrier) — "
                            + ("its confirm was already true before the write (not unique to this write's "
                               "outcome)" if pre_confirm else "no completion signal appeared")
                            + "; not proceeding to the next write")
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
                # Suffix-replan: the working prefix (steps[:i]) already drove us here, so re-author
                # ONLY the broken tail from the current page rather than relearning the whole flow.
                # Gated on a heal provider being present (auto mode) and the failed step being a READ
                # — a write is never LLM-re-driven under drift (it could double-submit).
                if provider is not None and not step.mutating:
                    _log.warning(
                        "replay: step %d %r failed (%s) — suffix-replanning the tail",
                        i, step.intent, note,
                    )
                    new_steps, authored_ok, replan_llm, replan_traces, _replan_wrote = await _author_steps(
                        session, goal, provider, governor, settings.max_steps, on_step=on_step,
                        block_mutations=True,  # a replay-repair must never perform a NEW write
                    )
                    llm += replan_llm
                    traces.extend(replan_traces)
                    # If the repair fired a write on the wire (a formless or cross-origin POST that
                    # block_mutations, being classifier-based, can't see), do NOT cache/keep it —
                    # re-replaying would re-submit. Fail loud instead. (Correct here too: the repair is
                    # not human-approved, so even an accidental write must never become a cached step.)
                    if _replan_wrote:
                        _log.warning("replay: suffix-replan fired a write — refusing to cache the repair")
                    elif new_steps or authored_ok:
                        flow = CachedFlow(
                            key=flow.key, goal=flow.goal, start_url=flow.start_url,
                            steps=list(flow.steps[:i]) + new_steps, created_ts=time.time(),
                        )
                        mode, dirty, replanned, success = "replay+replan", True, True, authored_ok
                        _log.info(
                            "replay: suffix-replanned %d new step(s) onto a %d-step prefix",
                            len(new_steps), i,
                        )
                        break
                success = False
                _log.warning("replay: step %d %r failed: %s", i, step.intent, note)
                break

        fin = await finalize(session) if finalize else None
        # A suffix-replan that reached the data page without the agent emitting `done` still solves
        # the goal when the finalize extraction succeeds — mirror _learn's finalize-solved upgrade.
        if replanned and not success and isinstance(fin, dict) and fin.get("solved"):
            success = True
        extra = {"finalize": fin} if finalize else {}
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


def _select_values(text: Optional[str]):
    """Decode a recorded `select` step's value. A multi-select is recorded as a JSON ARRAY of option values
    (see recorder._CAPTURE_JS); a single select as a bare string. Returns a list for the former (so
    `select_option` selects exactly that set and deselects the rest) or the string for the latter. A bare
    value that merely looks numeric (`"3"` -> int) is NOT a list, so it stays a single string. (Known narrow
    edge: a SINGLE-select whose option `value` attribute is itself a JSON array literal — e.g. `'["x"]'` —
    would be decoded as a multi-select set; option values are effectively never JSON arrays in real HTML, so
    this is an accepted residual rather than a flag on every step.)"""
    raw = text or ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    return parsed if isinstance(parsed, list) else raw


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
        drifted, reason = False, ""
        with tr.measure("gate"):
            if step.precond_scope and step.locator is not None:
                # PRECISE gate: did the target's enclosing form/section change? (ignores unrelated
                # page churn — banners, badges — that the whole-page fingerprint over-flags as drift).
                # For a refless submit (press), step.locator is the FOCUSED field captured at learn, so
                # this resolves that exact element and scopes ITS form — identity-stable, not focus-of-
                # the-moment.
                # unique=True: an AMBIGUOUS target — two structurally-identical forms sharing role+name
                # with no disambiguating test-id/id/unique-css (a header mini-login + a main login) — must
                # FAIL LOUD, not bind a blind `.first` whose identical form scope then fingerprint-matches
                # and waves the write through into the WRONG form. Same fail-on-ambiguity rationale as the
                # pinned-read gate (locators.resolve docstring).
                target = await resolve(page, step.locator, unique=True)
                if target is None:
                    drifted, reason = True, "mutation gate: target missing/ambiguous — refusing to re-drive a write"
                else:
                    current = await scope_fingerprint(target)
                    if current and current != step.precond_scope:
                        drifted, reason = True, "mutation gate: form/section drift — refusing to re-drive a write"
            else:
                # fallback (old flow, or a navigate/refless submit with no recorded scope)
                obs = await session.snapshot()
                if step.precond_fingerprint and obs.fingerprint != step.precond_fingerprint:
                    drifted, reason = True, "mutation gate: page drift — refusing to re-drive a write"
        if drifted:
            tr.meta["gate"] = "drift"
            return False, reason, False
        key = idempotency_key(scope, idx, step.intent)
        tr.meta["idempotency_key"] = key
        await session.set_extra_http_headers({"Idempotency-Key": key})

    # Bounded wait for a mutating step's (possibly async) write to leave the browser before the `finally`
    # clears the Idempotency-Key. Kept SHORT (write_settle_ms) so a no-write mutating step doesn't stall the
    # full action_timeout_ms, and FLOORED at 1ms so a 0/negative settle can't become Playwright's
    # expect_request "wait forever" sentinel (timeout=0) and hang a no-write step.
    write_settle_ms = max(1, min(settings.action_timeout_ms, settings.write_settle_ms))

    try:
        if step.action in ("press", "scroll", "navigate", "click_xy", "webmcp_call"):
            note = ""
            if step.action == "press" and step.locator is not None:
                # Re-establish the learned focus so the Enter-submit fires from the RIGHT field
                # (the gate just verified that field's form scope). Best-effort: the gate is the
                # safety check; pressing on the existing focus is the pre-existing fallback.
                # unique=True for symmetry with the gate above: never re-focus a blind `.first` when the
                # field is ambiguous (the gate already failed loud in that case — this just refuses to
                # silently re-focus a wrong-but-identical field on any path that reaches here).
                focus_loc = await resolve(page, step.locator, unique=True)
                if focus_loc is not None:
                    try:
                        await focus_loc.focus(timeout=settings.action_timeout_ms)
                    except Exception:  # noqa: BLE001 - couldn't focus -> press whatever's focused
                        pass
            act = Action(
                action=step.action, intent=step.intent, text=step.text,
                coords=step.coords, tool=step.tool, args=step.args,
            )
            async with governor.gate(origin):
                with tr.measure("act"):
                    try:
                        if step.action == "press" and step.mutating:
                            # A refless submit fires the form POST ASYNCHRONOUSLY: page.keyboard.press
                            # returns before the request leaves the browser. Await the in-flight write so
                            # the Idempotency-Key (set by the gate above) is still on the context when the
                            # POST is issued — otherwise the `finally` below clears it first and the write
                            # replays WITHOUT the dedupe key (a retried submit could double-submit). The wait
                            # is BOUNDED to write_settle_ms (a press-triggered write fires near-immediately; a
                            # no-write press must not stall the full action_timeout_ms), and tolerant of the
                            # no-write case (a JS-only submit fires no network request): the header was set
                            # throughout the act, so nothing is lost.
                            try:
                                async with page.expect_request(
                                    lambda r: is_write_request(r.method, r.url),
                                    timeout=write_settle_ms,
                                ):
                                    await session.act(act)
                            except PlaywrightTimeoutError:
                                pass
                        else:
                            await session.act(act)
                        return True, "", False
                    except Exception as exc:  # noqa: BLE001
                        note = f"{type(exc).__name__}"
            return await _maybe_heal(session, step, provider, tr, goal, note)

        if step.action in ("click", "type", "select") and step.locator is not None:
            with tr.measure("resolve"):
                # unique=True: never silently bind the first of several ambiguous matches — that could
                # click/type the WRONG element and return wrong data. The neighbor anchor disambiguates
                # most same-name cases; a genuinely ambiguous bind fails loud here -> heal (auto) or a
                # loud replay failure to re-learn, never a silent wrong-element actuation.
                loc = await resolve(page, step.locator, unique=True)
            if loc is None:
                return await _maybe_heal(
                    session, step, provider, tr, goal, "locator unresolved or ambiguous (drift)"
                )
            note = ""

            async def _actuate() -> None:
                if step.action == "click":
                    await loc.click(timeout=settings.action_timeout_ms)
                elif step.action == "select":  # recorder: re-select the recorded option(s) by value
                    await loc.select_option(_select_values(step.text), timeout=settings.action_timeout_ms)
                else:
                    await loc.fill(step.text or "", timeout=settings.action_timeout_ms)

            async with governor.gate(origin):
                with tr.measure("act"):
                    try:
                        if step.mutating:
                            # A mutating click/type/select can fire its write ASYNCHRONOUSLY — an
                            # onchange/oninput autosave fetch-POST, a <select onchange=form.submit()>, or a
                            # click handler that defers its fetch a tick — returning before the request leaves.
                            # Await the in-flight write so the Idempotency-Key the gate set is still on the
                            # context when the POST is issued (else the `finally` clears it first and a retried
                            # run could double-submit). The wait is BOUNDED to write_settle_ms (these writes
                            # fire near-immediately; a mutating step that fires NO write — a preventDefault'd
                            # submit, a client-only button — must not stall the full action_timeout_ms).
                            # Tolerant of the no-write case: the header was live for the whole act, so nothing
                            # is lost. (A form-submit click also auto-waits its navigation -> this only ADDS safety.)
                            try:
                                async with page.expect_request(
                                    lambda r: is_write_request(r.method, r.url),
                                    timeout=write_settle_ms,
                                ):
                                    await _actuate()
                            except PlaywrightTimeoutError:
                                pass
                        else:
                            await _actuate()
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
    if step.mutating:
        # A mutating step is NEVER LLM-healed (a re-click could double-submit). The drift gate
        # already fails loud on a changed precondition; this also covers a gate-passing step whose
        # click merely throws — fail loud for a human to re-learn + re-approve, don't re-drive it.
        return False, f"{note}; mutating step not healed (fail loud)", False
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
    # Re-validate: a click that produced no observable state change likely bound the WRONG
    # element — do NOT persist a possibly-corrupt locator into the cache (a `type` legitimately
    # leaves url/fingerprint unchanged, so it's exempt from this check).
    if action.action == "click":
        after = await session.snapshot()
        if not state_changed(obs, after):
            return False, f"{note}; heal had no effect — not persisted", True
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
