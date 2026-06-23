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
  - WRITE recording is intentionally out of scope here: a mutating step needs its `precond_scope` captured
    at record time for the write gate — noted in the design doc, not built in the spike.
  - The `demo` is a callable that drives the page; in a real product it's a human in a headed browser,
    in the test it's a scripted sequence of real interactions (so the spike stays key-less + deterministic).
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .locators import LocatorSpec
from .safety import classify_mutation
from .snapshot import _ACCNAME_JS, _ROLEOF_JS

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
  const ACTIONABLE = 'a[href],button,input,select,textarea,[role=button],[role=link],' +
                     '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[onclick]';
  const send = (action, el, value) => {
    if (el && el.nodeType === 1) { try { window.__ultracua_record({ action, spec: specOf(el), value }); } catch (e) {} }
  };
  // Capture phase, so we record BEFORE the click's default action (navigation/toggle). Map each click to
  // its nearest actionable ancestor; a click on non-actionable chrome is ignored. (A click on a wrapping
  // <label>'s text is ignored here — `closest` doesn't reach the child input — but the browser's synthetic
  // click ON the input is captured. The label/input double-fire across browsers is an untested edge; see
  // the spike doc.)
  document.addEventListener('click', (ev) => {
    const c = ev.target && ev.target.closest && ev.target.closest(ACTIONABLE);
    if (c) send('click', c, null);
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target, t = (el.type || '').toLowerCase();
    if ((el.tagName === 'INPUT' && t !== 'checkbox' && t !== 'radio') ||
        el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      send('type', el, el.value);  // checkbox/radio are captured by their click above
    }
  }, true);
})()"""

Demo = Callable[[object], Awaitable[None]]


def _step_from_event(ev: dict) -> CachedStep:
    spec = LocatorSpec(**ev["spec"])
    action = ev["action"]
    intent = f"{action} {spec.name or spec.tag}".strip()  # placeholder — real intents are an open question
    mutating = classify_mutation(action, intent, spec.name or "", {})
    return CachedStep(intent=intent, action=action, locator=spec,
                      text=ev.get("value") if action == "type" else None, mutating=mutating)


async def record_demo(
    url: str, demo: Demo, *, goal: str, cache: FlowCache, scope: str = "default",
    headless: bool = True, settle_ms: int = 80,
) -> CachedFlow:
    """Capture a demonstration of `goal` at `url` into a cached, replayable `CachedFlow`.

    `demo(page)` performs the flow (a human in a headed browser; a scripted sequence in tests). Each touched
    control is described into a resilient `LocatorSpec` at the moment it's acted on. Returns the cached flow.
    """
    session = await BrowserSession(headless=headless).start()
    events: list[dict] = []
    page = session.page
    assert page is not None
    await page.expose_function("__ultracua_record", lambda ev: events.append(ev))
    await page.add_init_script(_CAPTURE_JS)
    try:
        await session.goto(url)
        await demo(page)                       # the demonstration
        await page.wait_for_timeout(settle_ms)  # let the last exfiltration calls flush before teardown
    finally:
        await session.close()

    steps = [_step_from_event(ev) for ev in events]
    flow = CachedFlow(key=flow_key(goal, url, scope), goal=goal, start_url=url,
                      steps=steps, created_ts=time.time())
    cache.put(flow)
    return flow


def recorded_steps_summary(flow: CachedFlow) -> list[str]:
    """A human-readable line per recorded step (for `flow record`'s inspect output / the spike test)."""
    return [f"{s.action} {(s.locator.name if s.locator else '') or (s.locator.tag if s.locator else '')!r}"
            + (f" = {s.text!r}" if s.text else "") for s in flow.steps]
