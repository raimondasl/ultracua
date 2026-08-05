"""THE causal write-attribution machinery, shared verbatim by the recorder and the learn path.

WHY THIS MODULE EXISTS. Attribution — "which action caused this wire write?" — cannot be answered from
the clock. `flow._write_owner` tried, by arbitrating overlapping time windows, and with steps
milliseconds apart the candidate set is always {i-1, i}: nothing past the FIRST step is attributable
(R3.2). A 0.73.0 redesign replaced the arbitration with a drain and was measured to credit the WRONG
step — a 450ms debounce landed on step 1, an 800ms one on step 2, while the real commit was step 0 —
and was reverted. No constant works: a tail long enough to catch a deferred write is long enough to
overlap the next step.

The page itself knows. A write dispatched from a click's own handler leaves `fetch`/`XHR`/`sendBeacon`
or submits a form INSIDE that click's synchronous turn, and `__ucturn` counts the commits in that turn.
Exactly one commit in the turn -> that commit caused the write, whatever the clock says. Zero (a timer,
a debounce, an awaited round-trip) or more than one (a nested synthetic commit) -> the cause cannot be
proven, `attributedSeq()` returns null, and the caller must FAIL LOUD rather than guess. That refusal is
what makes the signal trustworthy.

WHY IT IS ITS OWN MODULE. `recorder.py` has carried this since Phase I and `learn()` never had it — the
"a guard that exists on a sibling path and was never applied to the mechanism" pattern that
docs/open-defects.md keeps naming. It is extracted rather than transcribed for R3.1's reason: `rowIdOf`
existed as two copies that had to agree, and by the time an audit looked they had silently diverged. The
recorder and the learn path must compute attribution IDENTICALLY or one of them is wrong, so there is
one definition and both include it verbatim. `tests/test_causal_attribution.py` asserts the inclusion.

WHAT A CALLER MUST PROVIDE. This snippet assumes it is spliced into an IIFE that has already bailed out
of sub-frames and de-duplicated itself. It defines `KEY`, `nextSeq`, `pushRec`, `noteCommit`,
`attributedSeq`, `recordWire` and `markForm`, and installs the fetch / XHR / sendBeacon / form-submit
patches. Every patch is FAIL-SAFE: it calls through to the native implementation with the original
`this` and arguments and returns or throws unchanged, doing its recording inside try/catch — an
attribution bug must never alter what the page sends or whether it sends it.
"""

from __future__ import annotations

# The sessionStorage event buffer and its monotonic sequence. sessionStorage rather than a module-level
# JS variable because a commit very often NAVIGATES: a form POST leaves as a document request, and an
# in-memory queue would be torn down with the document before Python could read it. The buffer survives
# same-origin navigation, so a navigating commit's marker is still there afterwards.
_BUFFER_JS = r"""
  const KEY = '__ucbuf';
  // `seq` is a monotonic id stamped on every event. It lives in sessionStorage (NOT a local) so it stays
  // UNIQUE across a same-origin navigation — the init-script re-runs on each page and a per-load local counter
  // would restart at 0 and collide with the prior page's ids in the concatenated event stream. The turn bookkeeping that drives
  // per-write attribution lives in `_TURN_JS` below.
  const nextSeq = () => {
    let n = 0;
    try { n = parseInt(sessionStorage.getItem('__ucseq') || '0', 10) || 0; } catch (e) {}
    if (!n && _memseq) n = _memseq;          // opaque origin: keep counting in memory instead
    n += 1;
    _memseq = n;
    try { sessionStorage.setItem('__ucseq', String(n)); } catch (e) {}
    return n;
  };
"""

# The turn bookkeeping, extracted so the recorder's rich `store()` and the learn path's minimal listener
# share ONE definition of what a commit is and when a turn ends. Callers get the seq back and record
# whatever shape they need under it.
_TURN_JS = r"""
  // `__uclast` is the seq of the most-recent COMMIT (click/press/select); `__ucturn` counts the commits
  // in the CURRENT synchronous turn. A wire write is attributed only to a turn holding EXACTLY ONE
  // commit — see `attributedSeq` below.
  let __uclast = null, __ucturn = 0;
  const COMMIT = { click: 1, press: 1, select: 1 };
  // Append one record to the buffer. Kept separate from `noteCommit` so a caller can push a rich record
  // (the recorder's specOf/scope/domain payload) or a minimal one under the same seq.
  // sessionStorage is the PREFERRED sink because a commit very often NAVIGATES — a form POST leaves as
  // a document request — and it survives same-origin navigation, so the marker is still there when
  // Python drains afterwards. But it is not always available: a `file://` page (and any opaque origin)
  // throws on access, and there the whole mechanism would silently record nothing. Fall back to an
  // in-memory array, accepting the known cost — a NAVIGATING commit's marker is lost with the document,
  // which shows up as a marker shortfall and REFUSES. Losing it loudly is fine; recording nothing at
  // all silently is not.
  let _mem = [], _memseq = 0;
  const _ssOk = () => {
    try { sessionStorage.getItem(KEY); return true; } catch (e) { return false; }
  };
  const pushRec = (rec) => {
    try {
      if (!_ssOk()) { _mem.push(rec); return; }
      const arr = JSON.parse(sessionStorage.getItem(KEY) || '[]');
      arr.push(rec);
      sessionStorage.setItem(KEY, JSON.stringify(arr));
    } catch (e) { try { _mem.push(rec); } catch (e2) {} }
  };
  // The drain both Python callers use: returns and CLEARS whichever sink is in play, so neither side has
  // to know which one that was.
  window.__ucdrain = () => {
    let out = [];
    try {
      if (_ssOk()) {
        out = JSON.parse(sessionStorage.getItem(KEY) || '[]');
        sessionStorage.removeItem(KEY);
      }
    } catch (e) {}
    try { if (_mem.length) { out = out.concat(_mem); _mem = []; } } catch (e) {}
    return out;
  };
  // Allocate this event's seq and, when it is a COMMIT, make it the turn's commit. Returns the seq.
  const noteCommit = (action) => {
    const seq = nextSeq();
    if (COMMIT[action]) {
      __uclast = seq;
      __ucturn += 1;
      // The turn ends when control returns to the event loop: reset on the next MACROTASK, so the count
      // stays live through this turn's synchronous code AND its microtask continuations, but a later
      // timer/await-network continuation runs in a fresh turn with __ucturn back to 0 (its write is
      // deferred, and therefore unattributable).
      setTimeout(() => { __ucturn = 0; }, 0);
    }
    return seq;
  };
  // Read-only view of the turn, for callers that must not double-count. Exposed rather than letting a
  // caller touch `__ucturn` directly so the shared block keeps sole ownership of the bookkeeping.
  const turnCommitCount = () => __ucturn;
"""

# The wire patches: what counts as a write, how a marker is stamped, and the four dispatch points.
_WIRE_JS = r"""
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
  // DOCUMENT-CLASS writes (a <form method=post> submission). These leave the browser as a NAVIGATION, not a
  // fetch/XHR, so none of the three patches above sees them and Playwright reports them with
  // `resource_type == "document"`. Without a marker they are invisible to the per-write reconciliation, and
  // a form POST fired from a non-actionable element (a <div>, an href-less <a>) or from a
  // `<button type=button>` calling `form.submit()` is then either captured as NO step or as a NON-mutating
  // one — while a SEPARATE, correctly-gated write elsewhere in the same flow keeps the belt-and-suspenders
  // `wire_write and not gated` refusal from firing. That masking is what let a demonstrated write be
  // silently dropped from the recipe, or re-fired ungated and un-keyed on every replay.
  //
  // The two submission paths are DISJOINT, so no form is ever double-markered:
  //   * `form.submit()` fires NO submit event — the prototype patch is the only thing that sees it;
  //   * native submission (a submit button, Enter) and `requestSubmit()` fire the event WITHOUT calling
  //     `.submit()` — the capture-phase listener is the only thing that sees those.
  // `recordWire` already filters GET/HEAD, resolves against document.baseURI, drops the fragment and stamps
  // `attributedSeq()`, so a form POST with no single commit in its turn correctly lands UNATTRIBUTED.
  const markForm = (form) => {
    try {
      if (!form || form.nodeType !== 1) return;
      // getAttribute, NOT the `.action` / `.method` IDL properties: a form containing a control NAMED
      // "action" or "method" CLOBBERS them (`form.action` then returns that <input> element, not a string),
      // which would resolve the marker to the wrong url and silently stop it matching the wire request —
      // failing OPEN. The raw attributes cannot be clobbered. `recordWire` resolves relative urls against
      // document.baseURI itself, and an absent action correctly means "submit to the current document".
      recordWire((form.getAttribute('method') || 'GET').toUpperCase(),
                 form.getAttribute('action') || document.baseURI, 'form');
    } catch (e) {}
  };
  try {
    const _fsubmit = HTMLFormElement.prototype.submit;
    if (typeof _fsubmit === 'function') {
      HTMLFormElement.prototype.submit = function () {
        try { markForm(this); } catch (e) {}
        return _fsubmit.apply(this, arguments);
      };
    }
  } catch (e) {}
  document.addEventListener('submit', (ev) => { try { markForm(ev.target); } catch (e) {} }, true);
"""

# THE shared snippet. One string, included verbatim by `recorder._CAPTURE_JS` and by the learn path's
# init script — assert the inclusion rather than hoping for it (see tests/test_causal_attribution.py).
WIRE_ATTRIB_JS = _BUFFER_JS + _TURN_JS + _WIRE_JS


# The LEARN path's init script: the shared machinery plus the smallest possible set of listeners that
# register a Playwright actuation as a COMMIT.
#
# Deliberately lighter than `recorder._CAPTURE_JS`, which also captures `specOf`/scope/domain for every
# event. Learn does not need the payload — it needs only "a gesture happened, here is its seq" — and
# keeping it minimal keeps typed VALUES out of sessionStorage entirely, so this adds no surface to the
# secret-handling rules.
#
# Only `click` and Enter register. `session.act` drives clicks and key presses for the actions that
# produce writes in practice; a `select_option` write would carry no commit in its turn, report
# unattributable, and be REFUSED. That is a stated bound, not an oversight — and it is no worse than
# today, where nothing past the first step is attributable at all.
LEARN_ATTRIB_JS = ("(() => { if (window.top !== window) return;"
                   " if (window.__ucattr) return; window.__ucattr = 1;") + WIRE_ATTRIB_JS + r"""
  // Capture phase, so the commit is registered BEFORE the page's own handler runs and therefore before
  // any write that handler dispatches — `attributedSeq()` must see `__ucturn === 1` when `fetch` is
  // called, which only holds if the commit was noted first.
  // AT MOST ONE COMMIT PER TURN, and this is where the learn path deliberately differs from the
  // recorder. Playwright performs exactly ONE actuation per step, so any further gesture in the same
  // synchronous turn is a CONSEQUENCE of that actuation, not a second user action — pressing Enter in a
  // form fires `keydown` AND the browser's synthetic `click` on the submit button, which would leave
  // `__ucturn === 2` and make every Enter-submit unattributable. (Measured: it refused every
  // `test_press_gate` login flow.) The recorder must NOT collapse these — a human really can trigger
  // nested commits through a wrapper, and there `__ucturn > 1` is the honest "cannot tell which".
  const noteOnce = (kind) => {
    try {
      if (turnCommitCount() > 0) return;          // this turn is already claimed by our own actuation
      pushRec({ action: '__commit', seq: noteCommit(kind) });
    } catch (e) {}
  };
  document.addEventListener('click', () => { noteOnce('click'); }, true);
  document.addEventListener('keydown', (ev) => {
    try { if (ev && ev.key === 'Enter') noteOnce('press'); } catch (e) {}
  }, true);
})();"""

# Prefer the in-page drain the shared block installs (it knows which sink is in use); fall back to the
# direct sessionStorage read if the script never installed, so a drain never throws.
_DRAIN_JS = ("() => { try { if (window.__ucdrain) return window.__ucdrain(); } catch (e) {}"
             " try { const v = JSON.parse(sessionStorage.getItem('__ucbuf') || '[]');"
             " sessionStorage.removeItem('__ucbuf'); return v; } catch (e) { return []; } }")


async def install(page) -> None:
    """Install the learn-path attribution script on every document of this page."""
    await page.add_init_script(LEARN_ATTRIB_JS)


async def drain(page) -> list:
    """Atomic in-page read+clear of the event buffer, in event order.

    Guarded the same way the recorder's drain is: a drain that races a navigation (execution context
    destroyed) leaves the queue intact for the next one, and the queue lives in sessionStorage precisely
    so a navigating commit's marker survives the teardown.
    """
    try:
        return await page.evaluate(_DRAIN_JS) or []
    except Exception:  # noqa: BLE001 - a drain must never break the run it is observing
        return []


def attribute(events: list, seq_to_step: dict) -> "tuple[set, int]":
    """Turn drained events into (steps that wrote, count of writes nothing could claim).

    `events` are the drained records; `seq_to_step` maps a commit's seq to the loop index of the step
    that was acting when it was recorded. Pure, so the RULE is testable without a browser — the timing
    lives in the caller, the decision lives here, which is the shape `_write_owner` established.

    A marker with a seq we cannot place is counted unattributed rather than dropped: an unexplained
    write is the thing that must fail loud, and silently discarding it is how a write ends up cached
    ungated.
    """
    from .safety import is_write_request

    wrote_by_step: set = set()
    unattributed = 0
    for ev in events:
        if not isinstance(ev, dict) or ev.get("action") != "__wirewrite":
            continue
        # THE SIBLING FILTER. `recordWire` marks every non-idempotent request regardless of host —
        # including `navigator.sendBeacon`, which is always a POST — so without this an analytics beacon
        # fired from an inert click's own handler is a perfectly attributable "write". Both other
        # consumers of this signal already filter: `flow._watch_request` and the recorder's marker loop.
        # This was the one that did not, which is exactly the shape docs/open-defects.md keeps naming.
        # Measured cost of omitting it: a beacon-firing filter click cached `mutating=True` while the
        # real commit cached as a read.
        if not is_write_request(str(ev.get("method") or "POST"), str(ev.get("url") or "")):
            continue
        seq = ev.get("seq")
        step = seq_to_step.get(seq) if isinstance(seq, int) else None
        if step is None:
            unattributed += 1        # deferred, a shared turn, or a commit we never saw -> refuse
        else:
            wrote_by_step.add(step)
    return wrote_by_step, unattributed
