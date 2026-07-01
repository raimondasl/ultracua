"""Phase-I RECORDER — learn a flow from a human DEMONSTRATION.

The discovery loop is measured-done; the remaining ~40% MiniWoB miss is a *capability* ceiling — tasks
(garbled-label checkboxes, ambiguous options) where the LLM can't reliably GROUND (pick the right element).
A demonstration removes grounding from the loop: a human clicks the right element, and we just read the
DOM node under that click. The pipeline is:

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
  - `intent` starts as a placeholder ("click qux"); `caption_intents` (one off-replay-path LLM call, opt-in
    via the `flow record` CLI — `record(caption=…)`) relabels each step with a concise, goal-grounded intent
    for self-heal hints, the inspect output, and the keyword side of `classify_mutation`. Capture itself stays
    key-less; replay stays 0-LLM.
  - WRITE recording is supported via `mutate=True`. A write the form/keyword classifier can see — a
    form-submit click, an Enter-submit press, a submitting/posting `<select>` — is captured WITH its
    `precond_scope` so it replays through the mutation gate. A write it CAN'T see (a formless fetch/XHR POST,
    a `sendBeacon`) is attributed PER-WRITE: the init-script instruments fetch / XMLHttpRequest.send /
    navigator.sendBeacon to emit a `__wirewrite` marker tying each non-idempotent request to the commit fired
    in its own synchronous turn, so the EXACT commit it caused is gated — no counting heuristic. A write whose
    cause is ambiguous (deferred/awaited, nested, background) or un-instrumentable (web-worker /
    service-worker / cross-realm — emits no marker but surfaces on the wire) is left UNATTRIBUTED and
    `flows.record` REFUSES the flow, never caching a write ungated. The caller routes a cached write through
    approval + idempotency like a learned write.
  - The `demo` is a callable that drives the page; in a real product it's a human in a headed browser,
    in the test it's a scripted sequence of real interactions (so the recorder stays key-less + deterministic).
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from .browser import BrowserSession
from .cache import CachedFlow, CachedStep, FlowCache, flow_key
from .llm.types import LLMRequest, Message, TextBlock, ToolDef
from .locators import _SPECOF_JS, LocatorSpec
from .safety import classify_mutation, is_write_request, origin_of
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
  // `seq` is a monotonic id stamped on every event. It lives in sessionStorage (NOT a local) so it stays
  // UNIQUE across a same-origin navigation — the init-script re-runs on each page and a per-load local counter
  // would restart at 0 and collide with the prior page's ids in the concatenated event stream. `__uclast`/
  // `__ucturn` drive PER-WRITE ATTRIBUTION (see attributedSeq): `__uclast` is the seq of the most-recent commit
  // (click/press/select), and `__ucturn` counts the commits in the CURRENT synchronous turn (reset on the next
  // macrotask). A wire-write is attributed ONLY to a turn that holds EXACTLY ONE commit; everything else
  // (deferred, or a nested synthetic commit sharing the turn) is left unattributed so `record` fails LOUD.
  let __uclast = null, __ucturn = 0;
  const COMMIT = { click: 1, press: 1, select: 1 };
  const nextSeq = () => {
    let n = 0;
    try { n = parseInt(sessionStorage.getItem('__ucseq') || '0', 10) || 0; } catch (e) {}
    n += 1;
    try { sessionStorage.setItem('__ucseq', String(n)); } catch (e) {}
    return n;
  };
  const store = (action, el, value, ctx, scope) => {
    if (el && el.nodeType !== 1) return;
    try {
      const seq = nextSeq();
      if (COMMIT[action]) {
        __uclast = seq;
        __ucturn += 1;
        // The turn ends when control returns to the event loop: reset on the next MACROTASK, so the count
        // stays live through this turn's synchronous code AND its microtask continuations, but a later
        // timer/await-network continuation runs in a fresh turn with __ucturn back to 0 (its write is deferred).
        setTimeout(() => { __ucturn = 0; }, 0);
      }
      const arr = JSON.parse(sessionStorage.getItem(KEY) || '[]');
      arr.push({ action, spec: el ? specOf(el) : null, value, ctx, scope, seq });
      sessionStorage.setItem(KEY, JSON.stringify(arr));
    } catch (e) {}
  };
  // PER-WRITE ATTRIBUTION. Patch the wire-write entry points — fetch / XMLHttpRequest.send /
  // navigator.sendBeacon — so each NON-IDEMPOTENT request pushes a `__wirewrite` marker tying it to the commit
  // that caused it. The Python side then gates EXACTLY that commit (no counting heuristic), so a submit click
  // whose write is SUPPRESSED (preventDefault / HTML5-validation-blocked) can't mask a separate formless POST.
  // Installed by add_init_script, which runs BEFORE any page script on every document — so a page can't first
  // capture an un-patched reference. Each patch is FAIL-SAFE: it calls through to the native impl with the
  // original `this`/args and returns or throws unchanged, recording inside try/catch — a recorder bug never
  // alters page behaviour. A WEB-WORKER / SERVICE-WORKER fetch/xhr write emits NO marker (the init-script
  // doesn't run there) but its request surfaces to Playwright, and the Python side fails LOUD on the per-url
  // shortfall (see the `xhr_urls` reconciliation), so such an un-instrumentable write is refused, never cached
  // ungated. SUB-FRAME (iframe) writes are DELIBERATELY excluded from that reconciliation: the init-script
  // bails in sub-frames, so counting their requests would false-refuse the many pages with a 3rd-party iframe
  // (chat/ad/analytics) that POSTs — and an iframe interaction is never a recorded main-frame step anyway.
  // (Residuals, all irreducible without per-request correlation: a WebSocket write-suspect carries no marker
  // and isn't reconciled — sockets can't be gated; an iframe/cross-realm write TRIGGERED by a recorded
  // main-frame action — e.g. via postMessage — could cache ungated; a non-surfacing marker and a worker write
  // at the EXACT same (method,url) still offset; a page that re-grabs the native impl from another realm;
  // `fetch.toString()` no longer reads `[native code]`. CSP/SRI don't apply — browser-injected.)
  const WRITE_METHODS = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };
  // Attribute a write to a commit ONLY when the cause is UNAMBIGUOUS: a SINGLE commit fired in the write's own
  // SYNCHRONOUS turn (its synchronous code + microtasks), i.e. __ucturn === 1. Otherwise stamp seq=null so the
  // Python side leaves the write UNATTRIBUTED and `record` fails LOUD (refuse to cache) rather than gate the
  // wrong step:
  //   - __ucturn === 0  -> a DEFERRED write (timer / awaited round-trip / load-or-interval handler). Its cause
  //     can't be proven in-page: a load-armed write coincides indistinguishably with an unrelated click, and a
  //     timer write may land after a LATER actuation. (Recovering the legit `await fetch(...); fetch(POST)`
  //     single-click case would need causal scheduling-time capture — patching setTimeout/Promise — too
  //     invasive to do without altering page behaviour; fail-loud is the safe choice.)
  //   - __ucturn > 1    -> a NESTED synthetic commit shares the turn (wrapper -> hidden control); we can't tell
  //     which commit issued the write.
  const attributedSeq = () => (__ucturn === 1 ? __uclast : null);
  // `src` (fetch/xhr/beacon) tags which entry point fired the marker. The Python side reconciles the
  // fetch+xhr markers against the fetch/xhr requests Playwright saw on the wire: a write from a web worker /
  // cross-realm context (which this init-script can't reach) surfaces as a fetch/xhr request with NO marker,
  // so a shortfall = an un-gateable worker write -> fail loud. Beacons are excluded from that reconciliation
  // (Playwright surfaces sendBeacon inconsistently, and workers can't sendBeacon).
  const recordWire = (method, url, src) => {
    try {
      const m = (method || 'GET').toUpperCase();
      if (!WRITE_METHODS[m]) return;   // GET/HEAD can't be a state-changing write -> not worth a marker
      // Resolve to the ABSOLUTE url (and drop the fragment, which never goes on the wire) so the marker's url
      // matches the request url Playwright reports — the Python side reconciles markers to wire requests by
      // (method, url). Resolve against document.baseURI (NOT location.href): the browser resolves a relative
      // fetch/XHR url against the document base, which a <base href> element overrides — so baseURI is what
      // matches the wire url. A relative or bad url that can't resolve stays as-is (it then matches no wire
      // request, which only makes the reconciliation MORE conservative — fail loud, never fail open).
      let u = String(url || '');
      try { u = new URL(u, document.baseURI).href; } catch (e) {}
      const hi = u.indexOf('#'); if (hi >= 0) u = u.slice(0, hi);
      const seq = attributedSeq();
      const arr = JSON.parse(sessionStorage.getItem(KEY) || '[]');
      arr.push({ action: '__wirewrite', method: m, url: u, src: src,
                 seq: (typeof seq === 'number' ? seq : null) });
      sessionStorage.setItem(KEY, JSON.stringify(arr));
    } catch (e) {}
  };
  try {
    const _fetch = window.fetch;
    if (typeof _fetch === 'function') {
      window.fetch = function (input, init) {
        try {
          const method = (init && init.method) ||
                         (input && typeof input === 'object' && input.method) || 'GET';
          // fetch accepts a string, a Request (string `.url`), OR a URL object (no `.url` — String() gives
          // its href). Mis-reading a URL object as '' collapsed the marker to the page root and false-refused.
          const url = (typeof input === 'string') ? input
                    : (input && typeof input.url === 'string') ? input.url
                    : (input != null ? String(input) : '');
          recordWire(method, url, 'fetch');
        } catch (e) {}
        return _fetch.apply(this, arguments);
      };
    }
  } catch (e) {}
  try {
    const _open = XMLHttpRequest.prototype.open;
    const _send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      try { this.__ucm = method; this.__ucu = url; } catch (e) {}
      return _open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      try { recordWire(this.__ucm, this.__ucu, 'xhr'); } catch (e) {}   // record at send -> __ucturn is current
      return _send.apply(this, arguments);
    };
  } catch (e) {}
  try {
    if (navigator.sendBeacon) {
      const _beacon = navigator.sendBeacon;
      navigator.sendBeacon = function (url) {
        try { recordWire('POST', url, 'beacon'); } catch (e) {}            // a beacon is always a POST
        return _beacon.apply(navigator, arguments);
      };
    }
  } catch (e) {}
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
      // A `type` is NOT a commit for per-write attribution (COMMIT = click/press/select), so an autosave-on-
      // change write fires in a turn with no commit (__ucturn===0) -> it is DEFERRED -> unattributed -> the
      // flow fails loud. No ctx/scope is needed on a `type`.
      store('type', el, el.value, null, null);  // checkbox/radio are captured by their click above
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
    // `change` never fires at all, losing the value entirely.) Mark the element so the change listener above
    // doesn't duplicate the type. The PRESS carries the ctx+scope (it is the commit); the type does not.
    store('type', el, el.value, null, null);
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
    # <button> reports submit=true with no form_method, and force-gating it would (a) over-gate a BENIGN
    # formless button and (b) pre-empt PER-WRITE ATTRIBUTION — a formless button whose handler fetch-POSTs is
    # gated precisely by its own `__wirewrite` marker (see record_demo), so we must not blanket-gate every
    # formless submit-typed button here. A POST-form submit is already caught by `classify_mutation`.
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


# (goal, [{action, name, text} per step]) -> [one concise intent per step, same order]. The captioner.
Caption = Callable[[str, list], Awaitable[list]]

_CAPTION_SYSTEM = (
    "You label each recorded browser UI step with a CONCISE imperative intent — what the user was trying to "
    "do — grounded in the GOAL. Use the verb a person would (e.g. 'place the order', 'tick the qux checkbox', "
    "'choose Banana', 'search for blue widgets'). For a COMMIT step, mirror the GOAL's action word "
    "(submit / place / save / delete / send / confirm) so a write reads as a write. Return EXACTLY one intent "
    "per step, in the given order, via the `caption` tool — same count, no extra prose, no step omitted."
)


async def caption_intents(router, goal: str, steps: list, *, tier: str = "strong",
                          max_tokens: int = 800) -> list:
    """Best-effort: ONE off-replay-path LLM call labeling each recorded step with a concise intent. Runs at
    RECORD time only (replay stays 0-LLM). `steps` is a list of `{action, name, text}` dicts. Returns one
    intent per step (same order) or `[]` on ANY failure or a count mismatch — the caller keeps its placeholder
    intents, so a captioner outage never breaks recording. The intent feeds self-heal hints, the inspect
    output, and (write flows) the keyword side of `classify_mutation` — never the replay locator."""
    if not steps:
        return []
    lines = []
    for i, s in enumerate(steps):
        bits = [f"{i}. {s.get('action')}"]
        if s.get("name"):
            bits.append(f"on {str(s['name'])[:60]!r}")
        if s.get("text"):
            bits.append(f"= {str(s['text'])[:60]!r}")
        lines.append(" ".join(bits))
    tool = ToolDef(
        name="caption", description="Return one concise intent per step, in order.",
        input_schema={"type": "object",
                      "properties": {"intents": {"type": "array", "items": {"type": "string"}}},
                      "required": ["intents"], "additionalProperties": False},
        strict=False,
    )
    try:
        req = LLMRequest(
            system=_CAPTION_SYSTEM, tools=[tool], force_tool="caption",
            messages=[Message("user", [TextBlock(f"GOAL: {goal}\n\nSTEPS:\n" + "\n".join(lines))])],
            max_tokens=max_tokens,
        )
        resp = await router.complete(req, tier=tier)
        tu = resp.tool_use("caption")
        intents = list((tu.input.get("intents") if tu is not None else None) or [])
    except Exception:  # noqa: BLE001 - best-effort; any failure -> keep placeholder intents
        return []
    if len(intents) != len(steps):
        return []  # count mismatch -> don't risk misaligning intents to steps
    return [str(x) for x in intents]


async def record_demo(
    url: str, demo: Demo, *, goal: str, cache: FlowCache, scope: str = "default",
    headless: bool = True, settle_ms: int = 80,
    prepare: Optional[Callable[[BrowserSession], Awaitable[None]]] = None,
    storage_state: Optional[str] = None, extra_headers: Optional[dict] = None,
    mutate: bool = False, caption: "Optional[Caption]" = None,
    window_size: Optional[tuple[int, int]] = None,
) -> "tuple[CachedFlow, bool, bool, int]":
    """Capture a demonstration of `goal` at `url` into a cached, replayable `CachedFlow`.

    `demo(page)` performs the flow (a human in a headed browser; a scripted sequence in tests). Each touched
    control is described into a resilient `LocatorSpec` at the moment it's acted on. `prepare(session)` runs
    after navigation, before the demo (the SAME hook replay uses, so the recorded locators land on the same
    DOM); `storage_state`/`extra_headers` seed auth so the demo runs in the same context as replay.

    Exfiltration is navigation-safe: events are written synchronously to an in-page `sessionStorage` queue
    and DRAINED post-navigation + at the end (no fixed-timeout flush). `settle_ms` only lets trailing
    DEBOUNCED events (a final scroll, an async `change`) flush before the last drain — it is no longer the
    correctness mechanism for the navigation race.

    `caption` (optional) is a best-effort `(goal, [{action,name,text}]) -> [intent]` callable (see
    `caption_intents`) run ONCE after capture to replace each placeholder intent with a concise, goal-grounded
    label — for self-heal hints, the inspect output, and (write flows) the keyword side of `classify_mutation`.
    It runs at RECORD time only, so replay stays 0-LLM; any failure leaves the placeholder intents.

    `mutate=True` marks this as a DECLARED write recording (the caller knows the demo writes and supplied a
    confirm check). It makes capture WRITE-GATE-SAFE: a form-submit click, an Enter-submit press, OR a
    submitting/posting `<select>` (any method, so a GET-form write is covered) is recorded as a mutating step
    carrying its `precond_scope`, so the existing replay mutation gate refuses it under form/section drift; and
    a write that no form/keyword signal caught (a formless fetch/XHR POST, a `sendBeacon`) is attributed
    PER-WRITE to the commit that caused it — the init-script monkeypatches fetch/XHR/sendBeacon to emit a
    `__wirewrite` marker carrying the seq of the commit fired in the write's own synchronous turn — so the
    EXACT commit it caused is gated. There is no counting heuristic, so a submit click whose write is suppressed
    can't mask a separate formless POST. A write whose cause is AMBIGUOUS (deferred timer/await, a nested
    synthetic commit, or a background load write) is left UNATTRIBUTED and `record` fails loud, never gating
    the wrong step.

    Returns `(flow, performed_write, crossed_origin, unattributed_writes)`. `performed_write` flags that the
    demo touched the WRITE surface — a **non-idempotent HTTP request** (POST/PUT/PATCH/DELETE, caught via
    `page.on("request")` — covers form submits + fetch/XHR) OR **any WebSocket frame sent** (a write-suspect,
    since read vs write isn't distinguishable over a socket). NOT auto-detected: a **side-effecting GET** (a
    write behind a GET — we trust HTTP method semantics, the same limitation as the engine's classifier) — that
    is caught only when the caller DECLARES the flow a write (`mutate=True`). `crossed_origin` flags that a
    CROSS-origin main-frame navigation occurred during the demo: the prior origin's not-yet-drained events
    (incl. the navigating click) are orphaned, so the recording may be silently truncated — the caller
    (`record`) FAILS LOUD rather than cache a possibly-incomplete flow. Same-origin multi-page flows are
    unaffected. `unattributed_writes` counts genuine wire writes (fetch/XHR/sendBeacon) that could be tied to
    no single commit (any deferred/nested/background write) — `record` refuses the flow when it is > 0 (a real
    write that would replay UNGATED), rather than cache it behind an unrelated gated step.
    """
    session = await BrowserSession(
        headless=headless, storage_state=storage_state, window_size=window_size
    ).start()
    events: list[dict] = []
    # `hit` drives performed_write + the un-gated guard. `xhr_urls` tallies non-idempotent fetch/xhr writes
    # seen on the wire, keyed by (method, url), reconciled against the in-page fetch/xhr markers (also keyed by
    # (method, url)) to catch a write from a context the init-script can't instrument: a web worker / cross-
    # realm fetch surfaces here but emits NO marker, so a per-url shortfall = an un-gateable write -> fail loud.
    # Keying by URL (not a global count) stops a NON-surfacing marker (an aborted / CSP-blocked / throwing
    # fetch that emits a marker but never hits the wire) from offsetting a real worker write at a DIFFERENT url.
    # Redirect HOPS are excluded (redirected_from set): a method-preserving 307/308 redirect of an instrumented
    # fetch is the SAME logical write the single JS call already markered once. Form submits are navigations
    # (resource_type "document") and beacons ("ping") are excluded — the former is classifier-gated, the latter
    # marker-gated and surfaced inconsistently. (Residual: a NON-surfacing marker and a worker write at the
    # EXACT same (method, url) still offset — irreducible without per-request correlation; and a WebSocket
    # write-suspect is outside this reconciliation — sockets carry no marker and can't be gated.)
    wrote: dict = {"hit": False}
    xhr_urls: dict = {}  # (METHOD, url) -> count of surfaced non-idempotent fetch/xhr writes (redirect hops excluded)
    nav = {"origin": None, "crossed": False}  # crossed=True iff a CROSS-origin main-frame navigation occurred
    page = session.page
    assert page is not None
    drain_tasks: list[asyncio.Future] = []

    def _mark_write() -> None:
        wrote["hit"] = True

    def _watch_request(req) -> None:  # a non-idempotent, non-telemetry HTTP request = a write the human did
        try:
            if is_write_request(req.method, req.url):
                _mark_write()
                # Count worker-capable write types (fetch/xhr), excluding redirect hops (the same logical write,
                # already markered once by its JS call). ONLY count requests from a context that either CAN
                # carry a marker (the TOP frame — where the init-script patched fetch/XHR) or is genuinely
                # un-instrumentable (a service worker, or a dedicated worker — both attributed to the top frame
                # / no frame). EXCLUDE SUB-FRAME requests: the init-script bails in sub-frames
                # (window.top !== window) so an iframe deterministically emits NO marker — counting it would
                # FALSE-REFUSE a normal page with a 3rd-party iframe (chat/ad/analytics) that POSTs. A service
                # worker request has no frame (req.frame raises), so detect it via req.service_worker first.
                if req.resource_type in ("fetch", "xhr") and req.redirected_from is None:
                    countable = req.service_worker is not None  # SW write -> un-gateable -> count (fail loud)
                    if not countable:
                        try:
                            countable = req.frame.parent_frame is None  # top frame (main realm + its workers)
                        except Exception:  # noqa: BLE001 - no frame -> a cross-realm write -> count conservatively
                            countable = True
                    if countable:
                        k = (req.method.upper(), req.url.split("#", 1)[0])
                        xhr_urls[k] = xhr_urls.get(k, 0) + 1
        except Exception:  # noqa: BLE001
            pass

    def _watch_ws(ws) -> None:  # any frame SENT over a socket is a write-suspect (can't tell read from write)
        try:
            ws.on("framesent", lambda *_: _mark_write())
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

    # CONTEXT scope (not page) for the request watcher: a Service Worker / cross-realm fetch is surfaced at the
    # context, NOT the page, so a page-scoped watcher would MISS a SW write entirely (no marker either -> cached
    # ungated, a fail-open). Context scope is a superset (page + workers + SW + any popup); extra wire
    # visibility only ever makes the per-url reconciliation fail MORE loud (a shortfall), never fail open.
    page.context.on("request", _watch_request)
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

    # PER-WRITE ATTRIBUTION. The init-script monkeypatches the wire-write entry points (fetch / XHR.send /
    # sendBeacon) to push a `__wirewrite` marker for each non-idempotent request, tagged with the seq of the
    # commit that UNAMBIGUOUSLY caused it (or null when the cause is ambiguous — see `attributedSeq` in the JS).
    # Pull those markers OUT of the actuation stream first (a marker is attribution metadata, never a replayable
    # step), then gate EXACTLY the commit each genuine write names — by seq, no counting heuristic. So a submit
    # click whose write is SUPPRESSED (preventDefault / HTML5-validation-blocked) can NEVER mask a separate
    # formless POST: each write gates its OWN commit independently (unlike the old all-or-nothing fallback,
    # which one already-mutating form-submit step could suppress, leaving a parallel formless write ungated).
    # Telemetry / idempotent requests are filtered by the SAME `is_write_request` the engine uses. A write whose
    # marker carries seq=null — an AMBIGUOUS cause: any DEFERRED write (timer / awaited round-trip / load
    # handler), a nested synthetic commit's turn, or one orphaned by a cross-origin hop — is left UNATTRIBUTED:
    # nothing is gated for it, and `flows.record` REFUSES the flow (fail loud) rather than gate the wrong step.
    # Only a write fired SYNCHRONOUSLY from its own single actuation is gated (the in-page signal can't prove a
    # deferred write's cause — a load-armed write coincides indistinguishably with an unrelated click).
    wire_writes = [ev for ev in events if ev.get("action") == "__wirewrite"]
    events = [ev for ev in events if ev.get("action") != "__wirewrite"]
    steps = [_step_from_event(ev, write_flow=mutate) for ev in events]  # 1:1 with `events` by index
    # `unattributed_writes` counts genuine wire writes that could NOT be tied to a gated commit, so
    # `flows.record` refuses the flow rather than cache a real write ungated. Two sources, both fail-LOUD:
    #   (1) a marker the JS tied to NO single commit (seq=null — a deferred/nested/background write); and
    #   (2) a WORKER / cross-realm write: the init-script can't instrument a web worker, so its fetch/xhr POST
    #       fires on the wire (seen by `_watch_request`) but emits NO marker. We reconcile PER (method, url):
    #       every MAIN-realm fetch/xhr write emits a marker at the same (method, url) Playwright reports, so a
    #       url seen on the wire MORE times than it was markered = an un-gateable worker write. Checked by
    #       COUNT (not the existence guard) so it catches a worker write even when another step is gated — the
    #       masking the old guard let through (`wire_write and not gated` is disarmed by any one gated step).
    #       Form submits are navigations (excluded from `xhr_urls`) so a gated form submit never false-refuses.
    unattributed_writes = 0
    if mutate and wire_writes:
        by_seq = {ev["seq"]: i for i, ev in enumerate(events)
                  if ev.get("seq") is not None and ev.get("action") in ("click", "press", "select")}
        for w in wire_writes:
            if not is_write_request(w.get("method") or "", w.get("url") or ""):
                continue  # a GET / telemetry beacon -> not a state-changing write
            i = by_seq.get(w.get("seq"))
            if i is None or not events[i].get("scope"):
                unattributed_writes += 1  # a real write tied to no gated commit -> record fails loud
                continue
            if not (steps[i].mutating and steps[i].precond_scope):  # don't re-gate a form-classified commit
                steps[i] = steps[i].model_copy(
                    update={"mutating": True, "precond_scope": hash_scope(events[i]["scope"])})
    if mutate:
        marker_urls: dict = {}  # (method, url) -> fetch/xhr markers; keyed identically to xhr_urls
        for w in wire_writes:
            if w.get("src") in ("fetch", "xhr") and is_write_request(w.get("method") or "", w.get("url") or ""):
                k = ((w.get("method") or "").upper(), (w.get("url") or "").split("#", 1)[0])
                marker_urls[k] = marker_urls.get(k, 0) + 1
        for k, n_wire in xhr_urls.items():  # a url seen on the wire more than it was markered = worker write
            unattributed_writes += max(0, n_wire - marker_urls.get(k, 0))
    # INTENT CAPTION (best-effort, OFF the replay path): replace each placeholder intent with a concise,
    # goal-grounded label. `events`/`steps` are still 1:1 by index here (before scroll-coalescing), so each
    # captioned intent and its event's scope/ctx line up. In a DECLARED write flow ONLY, a better intent may
    # UPGRADE the keyword side of `classify_mutation` (the spike's backstop for a bland-named CLIENT-SIDE
    # commit that fired no wire write, hence no attribution marker) — upgrade-only and gated on the step's own
    # scope, never downgrading a gate and never re-classifying a READ flow (a caption that invents a 'submit'
    # keyword must not false-refuse a benign read). The caption is cached, so the replay-time idempotency key
    # (derived from intent) stays stable across runs.
    if caption is not None and steps:
        # REDACT a `type` step's value from the caption summary: it is the literal text the human typed,
        # which can be a password / token / PII, and the captioner is an external LLM. The field's
        # accessible name + the goal are enough to label it ("enter the search query"). select/press/scroll
        # text (an option value / "Enter" / a scroll Y) is non-secret and kept for caption quality.
        summ = [{"action": s.action, "name": (s.locator.name if s.locator else ""),
                 "text": (None if s.action == "type" else s.text)} for s in steps]
        try:
            new_intents = await caption(goal, summ)
        except Exception:  # noqa: BLE001 - best-effort
            new_intents = None
        if new_intents and len(new_intents) == len(steps):
            for i, cap in enumerate(new_intents):
                cap = (str(cap) if cap is not None else "").strip()
                if not cap:
                    continue
                upd: dict = {"intent": cap}
                if mutate and not steps[i].mutating and events[i].get("scope"):
                    s = steps[i]
                    if classify_mutation(s.action, cap, (s.locator.name if s.locator else ""),
                                         events[i].get("ctx") or {}):
                        upd.update(mutating=True, precond_scope=hash_scope(events[i]["scope"]))
                steps[i] = steps[i].model_copy(update=upd)
    steps = _coalesce_scrolls(steps)
    flow = CachedFlow(key=flow_key(goal, url, scope), goal=goal, start_url=url,
                      steps=steps, created_ts=time.time())
    cache.put(flow)
    return flow, wrote["hit"], nav["crossed"], unattributed_writes


def recorded_steps_summary(flow: CachedFlow) -> list[str]:
    """A human-readable line per recorded step (for `flow record`'s inspect output / the spike test)."""
    return [f"{s.action} {(s.locator.name if s.locator else '') or (s.locator.tag if s.locator else '')!r}"
            + (f" = {s.text!r}" if s.text else "") for s in flow.steps]
