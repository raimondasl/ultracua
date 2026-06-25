"""Phase-I RECORDER — learn a flow from a human DEMONSTRATION (SPIKE / experimental).

The discovery loop is measured-done; the remaining ~40% MiniWoB miss is a *capability* ceiling — tasks
(garbled-label checkboxes, ambiguous options) where the LLM can't reliably GROUND (pick the right element).
A demonstration removes grounding from the loop: a human clicks the right element, and we just read the
DOM node under that click. This prototype proves the pipeline end-to-end:

    capture (inject a click/input listener) -> describe each touched element (a resilient LocatorSpec)
    -> assemble the SAME `CachedFlow` the replay engine already consumes -> replay 0-LLM.

Because the output is an ordinary `CachedFlow`, EVERYTHING downstream is reused unchanged: resolve() +
neighbor-anchor, the drift gate, self-heal, the mutation gate, pinned reads, the canary. The recorder is a
new authoring *front-end*, not a new engine.

SPIKE SCOPE (see docs/recorder-spike.md for the design + open questions + effort estimate):
  - Captures `click` (incl. checkbox/radio toggles) and `type` (text inputs) — enough to prove the claim
    on a grounding-hard SELECTION task. Scroll / press / file-upload / multi-tab are full-build items.
  - `intent` is a placeholder derived from the element ("click qux"); real intents (human label or a
    post-hoc LLM pass) are an open question.
  - WRITE recording is now supported via `mutate=True`: a demonstrated form-submit is captured WITH its
    `precond_scope` (computed inline, exactly as the learn path does) so it replays through the mutation
    gate; the caller (`flows.record`) routes it through approval + idempotency like a learned write.
  - The `demo` is a callable that drives the page; in a real product it's a human in a headed browser,
    in the test it's a scripted sequence of real interactions (so the spike stays key-less + deterministic).
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .locators import LocatorSpec
from .safety import classify_mutation, is_write_request
from .snapshot import _ACCNAME_JS, _MUTATION_CTX_JS, _ROLEOF_JS, SCOPE_JS, hash_scope

# Runs in the page (injected before any page script, re-installed on every navigation). On each click of
# an actionable control / each text-input change, it computes a LocatorSpec INLINE (reusing the shared
# role/accessible-name derivation, so the captured name matches what resolve() expects) and exfiltrates it
# via the bound `window.__ultracua_record`. Computing the spec at event time means a navigating click's
# target is described BEFORE it disappears.
_CAPTURE_JS = "(() => { if (window.__ucapt) return; window.__ucapt = 1;" + _ROLEOF_JS + _ACCNAME_JS + r"""
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const cssPath = (e) => {
    const parts = [];
    while (e && e.nodeType === 1 && parts.length < 5) {
      if (e.id) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let part = e.tagName.toLowerCase();
      const p = e.parentElement;
      if (p) {
        const sibs = Array.from(p.children).filter((c) => c.tagName === e.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(e) + 1) + ')';
      }
      parts.unshift(part); e = e.parentElement;
    }
    return parts.join(' > ');
  };
  const specOf = (el) => ({
    role: roleOf(el), name: nameOf(el), tag: el.tagName.toLowerCase(),
    elem_id: el.id || null, testid: el.getAttribute('data-testid'),
    placeholder: el.getAttribute('placeholder'),
    text: norm(el.innerText || el.textContent).slice(0, 80),
    css: cssPath(el), anchor: null, anchor_source: null,
  });
  // The SAME write-signal probes the LEARN path uses (imported verbatim from snapshot.py), computed
  // INLINE on the click target while it's present: `mutationCtx` -> {submit, form_method} (is it a form
  // submit, and with what HTTP method) and `scopeArray` -> the [role,name,tag] interactables of the
  // target's enclosing form/section. Python hashes that array with the SAME `hash_scope`, so a recorded
  // write's precond_scope matches the replay-time mutation gate byte-for-byte.
  const mutationCtx = """ + _MUTATION_CTX_JS + r""";
  const scopeArray = """ + SCOPE_JS + r""";
  const ACTIONABLE = 'a[href],button,input,select,textarea,[role=button],[role=link],' +
                     '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[onclick]';
  const send = (action, el, value, ctx, scope) => {
    if (el && el.nodeType === 1) {
      try { window.__ultracua_record({ action, spec: specOf(el), value, ctx, scope }); } catch (e) {}
    }
  };
  // Capture phase, so we record BEFORE the click's default action (navigation/toggle). Map each click to
  // its nearest actionable ancestor; a click on non-actionable chrome is ignored. (A click on a wrapping
  // <label>'s text is ignored here — `closest` doesn't reach the child input — but the browser's synthetic
  // click ON the input is captured. The label/input double-fire across browsers is an untested edge; see
  // the spike doc.) The write-signal (ctx + scope) is computed for every click here, while the element is
  // live; the Python side decides which clicks become gated mutating steps.
  document.addEventListener('click', (ev) => {
    const c = ev.target && ev.target.closest && ev.target.closest(ACTIONABLE);
    if (c) { let ctx = null, scope = null;
      try { ctx = mutationCtx(c); } catch (e) {}
      try { scope = scopeArray(c); } catch (e) {}
      send('click', c, null, ctx, scope); }
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target, t = (el.type || '').toLowerCase();
    if ((el.tagName === 'INPUT' && t !== 'checkbox' && t !== 'radio') ||
        el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      send('type', el, el.value, null, null);  // checkbox/radio are captured by their click above
    }
  }, true);
})()"""

Demo = Callable[[object], Awaitable[None]]


def _step_from_event(ev: dict, *, write_flow: bool = False) -> CachedStep:
    spec = LocatorSpec(**ev["spec"])
    action = ev["action"]
    intent = f"{action} {spec.name or spec.tag}".strip()  # placeholder — real intents are an open question
    ctx = ev.get("ctx") or {}
    mutating = classify_mutation(action, intent, spec.name or "", ctx)
    # In a DECLARED write flow, gate any FORM SUBMIT the method-classifier treats as a read — a GET-form
    # submit (a write-behind-a-GET, which `classify_mutation` calls idempotent): the user has told us this
    # flow writes, so its submit must replay through the mutation gate, not blind. (Closes the GET-write
    # residual the engine's HTTP-method classifier can't see.)
    if write_flow and action == "click" and ctx.get("submit"):
        mutating = True
    # Capture the precise mutation-gate precondition (the target's enclosing form/section interactables),
    # exactly as the learn path does — so a recorded write replays GATED. INVARIANT: a mutating step must
    # NEVER be cached without a precondition (an empty precond_scope AND empty precond_fingerprint makes the
    # replay gate a no-op -> the write fires blind / under drift). So for a DECLARED write we scope EVERY
    # mutating click — a formless / keyword-only / GET-method commit included — never just form submits.
    # For a READ recording we scope only a true FORM submit: a keyword-mutating step in a read demo is
    # refused by `record()` anyway, and form-submit-only keeps a non-form keyword button (e.g. a JS "Submit"
    # that fires no write) on the cheap whole-page-fingerprint path instead of a fragile whole-body scope.
    is_form_submit = bool(ctx.get("submit") and ctx.get("form_method"))
    precond_scope = hash_scope(ev.get("scope")) if (mutating and (write_flow or is_form_submit)) else ""
    return CachedStep(intent=intent, action=action, locator=spec,
                      text=ev.get("value") if action == "type" else None,
                      mutating=mutating, precond_scope=precond_scope)


async def record_demo(
    url: str, demo: Demo, *, goal: str, cache: FlowCache, scope: str = "default",
    headless: bool = True, settle_ms: int = 80,
    prepare: Optional[Callable[[BrowserSession], Awaitable[None]]] = None,
    storage_state: Optional[str] = None, extra_headers: Optional[dict] = None,
    mutate: bool = False,
) -> "tuple[CachedFlow, bool]":
    """Capture a demonstration of `goal` at `url` into a cached, replayable `CachedFlow`.

    `demo(page)` performs the flow (a human in a headed browser; a scripted sequence in tests). Each touched
    control is described into a resilient `LocatorSpec` at the moment it's acted on. `prepare(session)` runs
    after navigation, before the demo (the SAME hook replay uses, so the recorded locators land on the same
    DOM); `storage_state`/`extra_headers` seed auth so the demo runs in the same context as replay.

    `mutate=True` marks this as a DECLARED write recording (the caller knows the demo writes and supplied a
    confirm check). It makes capture WRITE-GATE-SAFE: a form-submit click (any method, so a GET-form write
    is covered) is recorded as a mutating step carrying its `precond_scope`, so the existing replay mutation
    gate refuses it under form/section drift; and if a write fires on the wire that no form-submit step
    carried (a formless fetch/XHR POST, a `sendBeacon`), the LAST actuated click is gated as a fallback —
    so even an undetected write replays THROUGH the gate (fail loud on drift) rather than blind.

    Returns `(flow, performed_write)`. `performed_write` flags that the demo touched the WRITE surface — a
    **non-idempotent HTTP request** (POST/PUT/PATCH/DELETE, caught via `page.on("request")` — covers form
    submits + fetch/XHR) OR **any WebSocket frame sent** (treated as a write-suspect, since read vs write
    isn't distinguishable over a socket). NOT auto-detected: a **side-effecting GET** (a write behind a GET —
    we trust HTTP method semantics, the same limitation as the engine's classifier) or a `navigator.sendBeacon`
    (Playwright surfaces it inconsistently) — those are caught only when the caller DECLARES the flow a write
    (`mutate=True`). A read demonstration (`mutate=False`) that nonetheless fires a write is surfaced via
    `performed_write` so the caller can refuse it.
    """
    session = await BrowserSession(headless=headless, storage_state=storage_state).start()
    events: list[dict] = []
    wrote = {"hit": False}
    page = session.page
    assert page is not None

    def _watch_request(req) -> None:  # a non-idempotent, non-telemetry HTTP request = a write the human did
        try:
            if is_write_request(req.method, req.url):
                wrote["hit"] = True
        except Exception:  # noqa: BLE001
            pass

    def _watch_ws(ws) -> None:  # any frame SENT over a socket is a write-suspect (can't tell read from write)
        try:
            ws.on("framesent", lambda *_: wrote.__setitem__("hit", True))
        except Exception:  # noqa: BLE001
            pass

    page.on("request", _watch_request)
    page.on("websocket", _watch_ws)
    if extra_headers:
        await session.set_extra_http_headers(extra_headers)
    await page.expose_function("__ultracua_record", lambda ev: events.append(ev))
    await page.add_init_script(_CAPTURE_JS)
    try:
        await session.goto(url)
        if prepare is not None:
            await prepare(session)
        await demo(page)                       # the demonstration
        await page.wait_for_timeout(settle_ms)  # let the last exfiltration calls flush before teardown
    finally:
        await session.close()

    steps = [_step_from_event(ev, write_flow=mutate) for ev in events]
    # Fallback gate for a DECLARED write whose write fired on the wire but no FORM-submit step carried it
    # (a formless fetch/XHR POST, or a sendBeacon): gate the LAST actuated click — the demonstrated COMMIT
    # — on its enclosing section, so this residual still replays through the mutation gate instead of blind.
    # `events`/`steps` are 1:1 by index, so the event's captured scope drives the step's precond_scope. The
    # scope is broad (often whole-body for a formless control) -> at worst more drift-refusals, never a
    # blind write. (Untriggered for a read demo, or a write already carried by a gated form-submit step.)
    if mutate and wrote["hit"] and not any(s.mutating for s in steps):
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].action == "click" and events[i].get("scope"):
                steps[i] = steps[i].model_copy(
                    update={"mutating": True, "precond_scope": hash_scope(events[i]["scope"])}
                )
                break
    flow = CachedFlow(key=flow_key(goal, url, scope), goal=goal, start_url=url,
                      steps=steps, created_ts=time.time())
    cache.put(flow)
    return flow, wrote["hit"]


def recorded_steps_summary(flow: CachedFlow) -> list[str]:
    """A human-readable line per recorded step (for `flow record`'s inspect output / the spike test)."""
    return [f"{s.action} {(s.locator.name if s.locator else '') or (s.locator.tag if s.locator else '')!r}"
            + (f" = {s.text!r}" if s.text else "") for s in flow.steps]
