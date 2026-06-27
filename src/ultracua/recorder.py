"""Phase-I RECORDER — learn a flow from a human DEMONSTRATION (SPIKE / experimental).

The discovery loop is measured-done; the remaining ~40% MiniWoB miss is a *capability* ceiling — tasks
(garbled-label checkboxes, ambiguous options) where the LLM can't reliably GROUND (pick the right element).
A demonstration removes grounding from the loop: a human clicks the right element, and we just read the
DOM node under that click. This prototype proves the pipeline end-to-end:

    capture (inject click/change/keydown/scroll listeners) -> describe each touched element (a resilient
    LocatorSpec) -> assemble the SAME `CachedFlow` the replay engine already consumes -> replay 0-LLM.

Because the output is an ordinary `CachedFlow`, EVERYTHING downstream is reused unchanged: resolve() +
neighbor-anchor, the drift gate, self-heal, the mutation gate, pinned reads, the canary. The recorder is a
new authoring *front-end*, not a new engine.

CAPTURE FIDELITY (see docs/recorder-spike.md):
  - `click` (incl. checkbox/radio toggles), `type` (text inputs), `select` (dropdowns by value), `press`
    (Enter-submit on a text input with no submit button — the common "type then Enter" pattern), and
    `scroll` (the absolute Y a scroll settled at, debounced + coalesced; a best-effort viewport restore,
    not a gated/verified step).
  - Exfiltration is NAVIGATION-SAFE: each event is written SYNCHRONOUSLY to `sessionStorage` (a store that
    survives same-origin navigation) and Python DRAINS it post-navigation + at the end — no fixed-timeout
    flush that could lose the last event before a page tears down. Residual: a CROSS-origin navigation
    orphans the prior origin's not-yet-drained events (its sessionStorage doesn't carry over); we drain on
    every navigation to shrink that window, and the single-origin portal flow — the target use case — is
    fully covered.
  - `intent` is a placeholder derived from the element ("click qux"); real intents (human label or a
    post-hoc LLM pass) are an open question.
  - WRITE recording is supported via `mutate=True`: a demonstrated form-submit (click) or Enter-submit
    (press) is captured WITH its `precond_scope` (computed inline, exactly as the learn path does) so it
    replays through the mutation gate; the caller (`flows.record`) routes it through approval + idempotency
    like a learned write.
  - The `demo` is a callable that drives the page; in a real product it's a human in a headed browser,
    in the test it's a scripted sequence of real interactions (so the spike stays key-less + deterministic).
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .locators import _SPECOF_JS, LocatorSpec
from .safety import classify_mutation, is_write_request, NONIDEMPOTENT_METHODS, origin_of
from .snapshot import _ACCNAME_JS, _MUTATION_CTX_JS, _ROLEOF_JS, SCOPE_JS, hash_scope

# Runs in the page (injected before any page script, re-installed on every navigation). On each actuation it
# computes a LocatorSpec INLINE via the SHARED `specOf` (the very same one DESCRIBE_JS uses on the learn
# path — imported from locators.py) so a recorded step resolves IDENTICALLY to a learned one AND gains the
# drift-resilient neighbor anchor; it then pushes the event onto a `sessionStorage` buffer. Writing to the
# store is SYNCHRONOUS and survives same-origin navigation, so a navigating click's event is durable BEFORE
# the page tears down — the Python side drains the store post-navigation and at the end (no fixed-timeout
# flush).
_CAPTURE_JS = ("(() => { if (window.top !== window) return;"  # capture only in the TOP frame: a sub-frame's
               # sessionStorage queue is never drained (the drain runs on the main frame), so capturing there
               # would orphan events. (iframe/shadow capture is a documented deferred item.)
               " if (window.__ucapt) return; window.__ucapt = 1;") + _ROLEOF_JS + _ACCNAME_JS + _SPECOF_JS + r"""
  // The SAME write-signal probes the LEARN path uses (imported verbatim from snapshot.py), computed
  // INLINE on the target while it's present: `mutationCtx` -> {submit, form_method} (is it a form submit,
  // and with what HTTP method) and `scopeArray` -> the [role,name,tag] interactables of the target's
  // enclosing form/section. Python hashes that array with the SAME `hash_scope`, so a recorded write's
  // precond_scope matches the replay-time mutation gate byte-for-byte.
  const mutationCtx = """ + _MUTATION_CTX_JS + r""";
  const scopeArray = """ + SCOPE_JS + r""";
  const ACTIONABLE = 'a[href],button,input,select,textarea,[role=button],[role=link],' +
                     '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=combobox],' +
                     '[role=listbox],[role=option],[role=switch],[onclick]';
  // SYNCHRONOUS exfiltration: append to a sessionStorage queue (durable across same-origin navigation).
  // The read+clear on the Python side is atomic in-page, so no event is lost between push and drain.
  const KEY = '__ucbuf';
  const store = (action, el, value, ctx, scope) => {
    if (el && el.nodeType !== 1) return;
    try {
      const arr = JSON.parse(sessionStorage.getItem(KEY) || '[]');
      arr.push({ action, spec: el ? specOf(el) : null, value, ctx, scope });
      sessionStorage.setItem(KEY, JSON.stringify(arr));
    } catch (e) {}
  };
  // Elements whose value we captured on Enter (see keydown) so the Enter-triggered `change` doesn't ALSO
  // record a duplicate `type` for the same value.
  const enterCaptured = new WeakMap();
  // Capture phase, so we record BEFORE the click's default action (navigation/toggle). Map each click to
  // its nearest actionable ancestor; a click on non-actionable chrome is ignored. (A click on a wrapping
  // <label>'s text is ignored here — `closest` doesn't reach the child input — but the browser's synthetic
  // click ON the input is captured.) The write-signal (ctx + scope) is computed for every click while the
  // element is live; the Python side decides which clicks become gated mutating steps.
  document.addEventListener('click', (ev) => {
    const c = ev.target && ev.target.closest && ev.target.closest(ACTIONABLE);
    // A native <select>'s click only opens its dropdown (pure mechanic) — the meaningful action is the
    // `change` it fires, captured below as a `select` step; recording the click too would add a redundant
    // step that re-opens the dropdown on replay. Skip it.
    if (c && c.tagName !== 'SELECT') { let ctx = null, scope = null;
      try { ctx = mutationCtx(c); } catch (e) {}
      try { scope = scopeArray(c); } catch (e) {}
      store('click', c, null, ctx, scope); }
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target, t = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT') {
      // A <select> can SUBMIT (onchange="this.form.submit()") or fetch-POST — i.e. it can be a WRITE. So
      // capture its ctx+scope inline (exactly like a click) so the Python side can gate it; otherwise a
      // select-driven write would replay UNGATED. A <select multiple> reports only its first value via
      // `.value`, so encode the full selected set as a JSON array (decoded to select_option([...]) on replay).
      let ctx = null, scope = null;
      try { ctx = mutationCtx(el); } catch (e) {}
      try { scope = scopeArray(el); } catch (e) {}
      const val = el.multiple
        ? JSON.stringify(Array.prototype.map.call(el.selectedOptions, (o) => o.value))
        : el.value;
      store('select', el, val, ctx, scope);
    } else if ((el.tagName === 'INPUT' && t !== 'checkbox' && t !== 'radio') || el.tagName === 'TEXTAREA') {
      // Suppress the `change` that an Enter-submit fires for a value we already captured on keydown (below),
      // so the field isn't typed twice.
      if (enterCaptured.has(el) && enterCaptured.get(el) === el.value) { enterCaptured.delete(el); return; }
      // A `change` can SUBMIT/POST too (an autosave-on-change input), i.e. a `type` can be a WRITE — so
      // capture its ctx+scope inline, SYMMETRIC with the select branch, so the Python gate-all fallback can
      // gate a type-driven write. (A type without a wire write stays non-mutating; the scope goes unused.)
      let ctx = null, scope = null;
      try { ctx = mutationCtx(el); } catch (e) {}
      try { scope = scopeArray(el); } catch (e) {}
      store('type', el, el.value, ctx, scope);  // checkbox/radio are captured by their click above
    }
  }, true);
  // Enter-submit on a TEXT input: the "type then Enter" pattern. We capture it ONLY when no synthetic
  // submit-button click will ALSO fire (a form with a submit control submits via a synthesized click on
  // it — already captured by the click listener; recording a press too would double-submit on replay).
  // A <textarea>'s Enter inserts a newline (captured via change), and a button/link Enter becomes a click.
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    const el = ev.target;
    if (!el || el.nodeType !== 1 || el.tagName !== 'INPUT') return;
    const t = (el.type || '').toLowerCase();
    if (['checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file'].includes(t)) return;
    // If the form has ANY submit control, Enter triggers a synthesized CLICK on it (captured by the click
    // listener) -> recording the press TOO would submit twice on replay. Use `form.elements` (the
    // association-aware collection: it includes controls linked via the `form=` attribute that live OUTSIDE
    // the <form>, which `form.querySelector` would miss) so a stray external submit can't slip through.
    const form = el.form;
    if (form && Array.prototype.some.call(form.elements, (e) => {
      const tg = e.tagName, ty = (e.getAttribute('type') || '').toLowerCase();
      return (tg === 'BUTTON' && ty !== 'button' && ty !== 'reset') ||
             (tg === 'INPUT' && (ty === 'submit' || ty === 'image'));
    })) return;
    let ctx = null, scope = null;
    try { ctx = mutationCtx(el); } catch (e) {}
    try { scope = scopeArray(el); } catch (e) {}
    // Record the field's CURRENT value as a `type` step FIRST, then the `press` — so replay fills the field
    // BEFORE pressing Enter. (Enter fires keydown before the submit-triggered `change`, so without this the
    // cached order would be [press, type] and replay would submit an EMPTY field; for a formless input
    // `change` never fires at all, losing the value entirely.) Mark the element so the change listener
    // above doesn't duplicate the type. The type carries the SAME ctx+scope as the press, so if the type
    // itself triggers a write the gate-all fallback can gate it.
    store('type', el, el.value, ctx, scope);
    enterCaptured.set(el, el.value);
    store('press', el, 'Enter', ctx, scope);
  }, true);
  // Scroll: debounced so a flick of the wheel becomes ONE event at the Y it settled at — a BEST-EFFORT
  // viewport restore on replay (an absolute scrollTo that clamps silently; not gated/verified — downstream
  // resolve() + Playwright auto-scroll-into-view is what keeps actuation correct). Consecutive scrolls are
  // further coalesced on the Python side.
  let _st = null;
  document.addEventListener('scroll', () => {
    if (_st) clearTimeout(_st);
    _st = setTimeout(() => {
      const y = window.scrollY || (document.documentElement && document.documentElement.scrollTop) || 0;
      store('scroll', null, String(Math.round(y)), null, null);
    }, 100);
  }, true);
})()"""

Demo = Callable[[object], Awaitable[None]]


def _step_from_event(ev: dict, *, write_flow: bool = False) -> CachedStep:
    raw = ev.get("spec")
    spec = LocatorSpec(**raw) if raw else None  # scroll has no target element
    action = ev["action"]
    name = (spec.name or spec.tag) if spec else action
    intent = f"{action} {name}".strip()  # placeholder — real intents are an open question
    ctx = ev.get("ctx") or {}
    mutating = classify_mutation(action, intent, spec.name if spec else "", ctx)
    # In a DECLARED write flow, gate a COMMIT the method-classifier treats as a read — a GET-form submit (a
    # write behind a GET). Require BOTH `submit` AND `form_method`, i.e. a real FORM submit: a bare formless
    # <button> reports submit=true with no form_method, and force-gating it (a) over-gates a BENIGN formless
    # button and (b) — worse — makes it `mutating`, which SHORT-CIRCUITS the wire-write fallback below and
    # could leave the real (e.g. type-autosave) write ungated. A POST-form submit is already caught by
    # `classify_mutation`; a formless write (bland-named button, autosave) is caught by the gate-all fallback.
    if write_flow and action == "click" and ctx.get("submit") and ctx.get("form_method"):
        mutating = True
    if write_flow and action in ("press", "select") and ctx.get("form_method"):
        mutating = True
    # Capture the precise mutation-gate precondition (the target's enclosing form/section interactables),
    # exactly as the learn path does — so a recorded write replays GATED. INVARIANT: a mutating step must
    # NEVER be cached without a precondition (an empty precond_scope AND empty precond_fingerprint makes the
    # replay gate a no-op -> the write fires blind / under drift). So for a DECLARED write we scope EVERY
    # mutating commit — a formless / keyword-only / GET-method one included — never just form submits. For a
    # READ recording we scope only a true FORM submit: a keyword-mutating step in a read demo is refused by
    # `record()` anyway, and form-submit-only keeps a non-form keyword button (a JS "Submit" that fires no
    # write) on the cheap whole-page-fingerprint path instead of a fragile whole-body scope.
    is_form_submit = bool(ctx.get("submit") and ctx.get("form_method"))
    precond_scope = hash_scope(ev.get("scope")) if (mutating and (write_flow or is_form_submit)) else ""
    text = ev.get("value") if action in ("type", "select", "press", "scroll") else None
    return CachedStep(intent=intent, action=action, locator=spec, text=text,
                      mutating=mutating, precond_scope=precond_scope)


def _fires_counted_write(ev: dict) -> bool:
    """Does this event DEFINITELY fire a counted (non-idempotent) wire write — i.e. submit a POST/PUT/PATCH/
    DELETE form? Used to tally how many observed wire writes the classifier already accounts for. Counts ONLY
    the canonical cases that are sure to issue the write: a submit CLICK in a non-idempotent form, and an
    Enter PRESS in one (a captured press means the form had no submit button, so Enter submits it). A GET-form
    submit is excluded (it fires no counted write, so it must NOT offset the write tally and mask a real POST);
    a `<select>` is excluded (we can't tell if onchange submits). Deliberately UNDER-counts vs the true write
    so the fallback gate over-gates rather than masks. RESIDUAL (exotic, pre-existing): a submit click whose
    native submit is SUPPRESSED — JS `preventDefault`, an HTML5-validation block — fires no write yet still
    counts here, so it can COINCIDENTALLY offset a separate unaccounted formless POST (accounted == count) and
    leave that POST ungated. Closing it needs per-write attribution (instrument fetch/XHR), a separate effort;
    the human approval gate is the backstop. The realistic masker (a GET-form search + a formless save) is
    closed by the form-method exclusion above."""
    c = ev.get("ctx") or {}
    if (c.get("form_method") or "").lower() not in NONIDEMPOTENT_METHODS:
        return False
    return ev.get("action") == "press" or (ev.get("action") == "click" and bool(c.get("submit")))


def _coalesce_scrolls(steps: list[CachedStep]) -> list[CachedStep]:
    """Collapse each run of consecutive `scroll` steps to its LAST (the final settled Y) — intermediate
    scroll positions in one continuous scroll add no replay value and only inflate the step count."""
    out: list[CachedStep] = []
    for s in steps:
        if s.action == "scroll" and out and out[-1].action == "scroll":
            out[-1] = s
        else:
            out.append(s)
    return out


async def record_demo(
    url: str, demo: Demo, *, goal: str, cache: FlowCache, scope: str = "default",
    headless: bool = True, settle_ms: int = 80,
    prepare: Optional[Callable[[BrowserSession], Awaitable[None]]] = None,
    storage_state: Optional[str] = None, extra_headers: Optional[dict] = None,
    mutate: bool = False,
) -> "tuple[CachedFlow, bool, bool]":
    """Capture a demonstration of `goal` at `url` into a cached, replayable `CachedFlow`.

    `demo(page)` performs the flow (a human in a headed browser; a scripted sequence in tests). Each touched
    control is described into a resilient `LocatorSpec` at the moment it's acted on. `prepare(session)` runs
    after navigation, before the demo (the SAME hook replay uses, so the recorded locators land on the same
    DOM); `storage_state`/`extra_headers` seed auth so the demo runs in the same context as replay.

    Exfiltration is navigation-safe: events are written synchronously to an in-page `sessionStorage` queue
    and DRAINED post-navigation + at the end (no fixed-timeout flush). `settle_ms` only lets trailing
    DEBOUNCED events (a final scroll, an async `change`) flush before the last drain — it is no longer the
    correctness mechanism for the navigation race.

    `mutate=True` marks this as a DECLARED write recording (the caller knows the demo writes and supplied a
    confirm check). It makes capture WRITE-GATE-SAFE: a form-submit click, an Enter-submit press, OR a
    submitting/posting `<select>` (any method, so a GET-form write is covered) is recorded as a mutating step
    carrying its `precond_scope`, so the existing replay mutation gate refuses it under form/section drift; and
    if a write fires on the wire that no commit step carried (a formless fetch/XHR POST, a `sendBeacon`), EVERY
    actuated step with a captured scope is gated as a fallback (an async write can't be attributed to one UI
    event, so we over-gate rather than risk one slipping through) — so even an undetected write replays THROUGH
    the gate (fail loud on drift) rather than blind.

    Returns `(flow, performed_write, crossed_origin)`. `performed_write` flags that the demo touched the WRITE
    surface — a **non-idempotent HTTP request** (POST/PUT/PATCH/DELETE, caught via `page.on("request")` —
    covers form submits + fetch/XHR) OR **any WebSocket frame sent** (a write-suspect, since read vs write
    isn't distinguishable over a socket). NOT auto-detected: a **side-effecting GET** (a write behind a GET —
    we trust HTTP method semantics, the same limitation as the engine's classifier) or a `navigator.sendBeacon`
    (Playwright surfaces it inconsistently) — those are caught only when the caller DECLARES the flow a write
    (`mutate=True`). `crossed_origin` flags that a CROSS-origin main-frame navigation occurred during the demo:
    the prior origin's not-yet-drained events (incl. the navigating click) are orphaned, so the recording may
    be silently truncated — the caller (`record`) FAILS LOUD rather than cache a possibly-incomplete flow.
    Same-origin multi-page flows are unaffected.
    """
    session = await BrowserSession(headless=headless, storage_state=storage_state).start()
    events: list[dict] = []
    # COUNT distinct wire writes (not just a bool): the fallback gate compares this to the number of steps the
    # classifier already gated, so it can tell when there are MORE writes on the wire than commits it caught
    # (an unaccounted formless write) vs exactly one accounted write (no over-gating needed). `ws` counts as
    # at most 1 so a chatty socket can't inflate the count.
    wrote = {"http": 0, "ws": False}
    nav = {"origin": None, "crossed": False}  # crossed=True iff a CROSS-origin main-frame navigation occurred
    page = session.page
    assert page is not None
    drain_tasks: list[asyncio.Future] = []

    def _write_count() -> int:
        return wrote["http"] + (1 if wrote["ws"] else 0)

    def _watch_request(req) -> None:  # a non-idempotent, non-telemetry HTTP request = a write the human did
        try:
            if is_write_request(req.method, req.url):
                wrote["http"] += 1
        except Exception:  # noqa: BLE001
            pass

    def _watch_ws(ws) -> None:  # any frame SENT over a socket is a write-suspect (can't tell read from write)
        try:
            ws.on("framesent", lambda *_: wrote.__setitem__("ws", True))
        except Exception:  # noqa: BLE001
            pass

    async def _drain() -> None:
        # Atomic in-page read+clear of the capture queue, appended in event order. Guarded: a drain that
        # races a navigation (execution context destroyed) leaves the queue intact for a later drain.
        try:
            batch = await page.evaluate(
                "(k) => { const v = JSON.parse(sessionStorage.getItem(k) || '[]');"
                " sessionStorage.removeItem(k); return v; }",
                "__ucbuf",
            )
        except Exception:  # noqa: BLE001
            return
        if batch:
            events.extend(batch)

    def _on_nav(frame) -> None:
        # Drain post-navigation: sessionStorage persists across SAME-ORIGIN navigation, so draining as we go
        # captures each segment before a later CROSS-origin navigation could orphan it. The event callback is
        # sync, so schedule the async drain; the tasks are awaited before close.
        if frame is not page.main_frame:
            return
        drain_tasks.append(asyncio.ensure_future(_drain()))
        # Track main-frame origin transitions. A CROSS-origin hop orphans the prior origin's not-yet-drained
        # events (sessionStorage is per-origin) INCLUDING the navigating click itself — so the recording may
        # be silently truncated. We can't recover those post-nav, so we FLAG it and `record()` fails loud
        # rather than cache a possibly-incomplete flow. (Same-origin multi-page flows are unaffected.)
        u = frame.url or ""
        if not u or u.startswith("about:"):
            return
        o = origin_of(u)
        if nav["origin"] is None:
            nav["origin"] = o
        elif o != nav["origin"]:
            nav["crossed"] = True
            nav["origin"] = o

    page.on("request", _watch_request)
    page.on("websocket", _watch_ws)
    page.on("framenavigated", _on_nav)
    if extra_headers:
        await session.set_extra_http_headers(extra_headers)
    await page.add_init_script(_CAPTURE_JS)
    try:
        await session.goto(url)
        if prepare is not None:
            await prepare(session)
        await demo(page)                                  # the demonstration
        await page.wait_for_timeout(max(settle_ms, 150))  # let trailing debounced scroll / async change flush
    finally:
        page.remove_listener("framenavigated", _on_nav)   # stop scheduling drains before we drain + close
        for t in list(drain_tasks):
            try:
                await t
            except Exception:  # noqa: BLE001
                pass
        await _drain()                                    # final drain BEFORE close (the queue lives in-page)
        await session.close()

    steps = [_step_from_event(ev, write_flow=mutate) for ev in events]  # 1:1 with `events` by index
    # Fallback gate for a DECLARED write whose write fired on the wire but no COMMIT step carried it (a formless
    # fetch/XHR POST, or a sendBeacon): an async wire write CANNOT be attributed to the single UI event that
    # caused it (the page's fetch/XHR isn't tied to its trigger without instrumenting it, and a cross-clock
    # event-ts-vs-write-time compare is unreliable — Python's wall clock is coarse on some platforms). So when
    # there are MORE wire writes than the classifier already gated (`_write_count() > classified`), at least
    # one write is unaccounted: we conservatively gate EVERY remaining actuated step (click/type/select/press)
    # that carries a scope — `type` INCLUDED, since an autosave input is itself a write. Whichever one fired
    # the extra write is then gated; a benign step only gains a SUPERFLUOUS drift check. Over-gating is the
    # SAFE direction: a gated step actuates exactly ONCE regardless of the flag (the flag only ADDS a drift
    # gate + idempotency header + disables self-heal), so it can never double-submit; its only cost is a
    # benign step whose own section drifts then fails LOUD instead of self-healing. The count comparison keeps
    # the COMMON single-write flow (one classified commit, one wire write) from over-gating its benign fields.
    # We tally ONLY commits that DEFINITELY fired a counted write (`_fires_counted_write`: a non-idempotent
    # form submit/Enter) — NOT every mutating step: a GET-form submit is classified mutating yet fires no
    # counted write, so counting it would let it OFFSET (mask) a separate unaccounted formless POST and cache
    # it ungated. If the unaccounted write has NO scoped actuated step to pin it to, nothing is gated ->
    # `flows.record` refuses (wire write, nothing gated). `events`/`steps` are 1:1 here (pre scroll-coalesce).
    accounted = sum(1 for ev in events if _fires_counted_write(ev))
    if mutate and _write_count() > accounted:
        for i, ev in enumerate(events):
            if ev.get("action") in ("click", "type", "select", "press") and ev.get("scope") \
                    and not steps[i].mutating:
                steps[i] = steps[i].model_copy(
                    update={"mutating": True, "precond_scope": hash_scope(events[i]["scope"])}
                )
    steps = _coalesce_scrolls(steps)
    flow = CachedFlow(key=flow_key(goal, url, scope), goal=goal, start_url=url,
                      steps=steps, created_ts=time.time())
    cache.put(flow)
    return flow, _write_count() > 0, nav["crossed"]


def recorded_steps_summary(flow: CachedFlow) -> list[str]:
    """A human-readable line per recorded step (for `flow record`'s inspect output / the spike test)."""
    return [f"{s.action} {(s.locator.name if s.locator else '') or (s.locator.tag if s.locator else '')!r}"
            + (f" = {s.text!r}" if s.text else "") for s in flow.steps]
